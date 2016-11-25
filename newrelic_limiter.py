#!/usr/bin/env python
import boto.ec2
import boto.ec2.elb
from subprocess import Popen
import subprocess
import json
import sys
import re
from fabric.api import settings, run, env, hide
import datetime
import time

# Vars
desired_nrelic_nbr = 6 # newrelic agent number you want
nrelic_api_key= ""# your newrelic api key
nrelic_app_id= ""# newrelic app id
region = "eu-west-1" # your aws region
list_ip_newrelic = []
elb_name = "" # your ELB NAME (must be uniq)
inst_tag_name = "" #your instance tag name (must be uniq)
list_nrelic_status = {}
# fix Exception in thread Thread-49
env.eagerly_disconnect = True
unix_epoch = datetime.datetime(1970, 1, 1)
inst_dict = {}
env.key_filename = "/path/ssh/key/private" # path to your ssh private key

# you need aws cli configure with a prod profile name
try:
    connection_ec2 = boto.ec2.connect_to_region(region, profile_name="prod")
    connection_elb = boto.ec2.elb.connect_to_region(region, profile_name="prod")
    my_instances = connection_ec2.get_all_instances()
except:
    connection_ec2 = boto.ec2.connect_to_region(region)
    connection_elb = boto.ec2.elb.connect_to_region(region)
    my_instances = connection_ec2.get_all_instances()


class bcolors:
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'


def reformat_date(date_raw):
    del_suffix = re.sub('.000Z', '',date_raw)
    del_T =  re.sub('T', ' ',del_suffix)
    log_dt = datetime.datetime.strptime(del_T, "%Y-%m-%d %H:%M:%S")
    seconds_from_epoch = (log_dt - unix_epoch).total_seconds()
    return seconds_from_epoch



def nrelic_check(srv_ip):
    cmd = 'root@' + srv_ip
    with settings(host_string=cmd):
        try:
            result = run("service newrelic-daemon status", pty=False, shell=False, warn_only=True, timeout=2)
            if re.search("running", result):
                inst_dict.append('running')
                list_nrelic_status[srv_ip] = 'running'
            elif re.search("stop", result):
                list_nrelic_status[srv_ip] = 'stop'
                inst_dict.append('stop')
                print "STOP"
        except: Exception


def current_elb():
    all_elb = connection_elb.get_all_load_balancers()
    for elb in all_elb:
        if re.search(elb_name, str(elb), re.IGNORECASE):
            cur_elb = elb
    return cur_elb


# renvoie un dict {inst_id:[ip,launch_time,tag,status}
def list_ip_all_instance():
    global inst_dict
    inst_dict = {}
    for instance in my_instances:
        instance_per_reserv = len(instance.instances)
        for i in range(instance_per_reserv):
            try:
                if inst_tag_name == instance.instances[i].tags['Name']:
                    inst_id = instance.instances[i].id
                    inst_ip = instance.instances[i].private_ip_address
                    inst_launch_time = reformat_date(instance.instances[i].launch_time)
                    if inst_id is None or inst_ip is None or inst_launch_time is None:
                        print "do nothing"
                        print "do nothing" + instance.instances[i].private_ip_address
                    else:
                        inst_dict[inst_id] = []
                        inst_dict[inst_id].append(inst_ip)
                        inst_dict[inst_id].append(inst_launch_time)
                        if instance.instances[i].tags['newrelic'] == "1":
                            inst_dict[inst_id].append('tag_ok')
                        else:
                            inst_dict[inst_id].append('tag_ko')
            except:
                pass
    # check newrelic status
    for k, v in inst_dict.iteritems():
        srv_ip = inst_dict[k][0]
        cmd = 'root@' + srv_ip
        with settings(host_string=cmd):
            try:
                print bcolors.BLUE
                with hide('warnings'):
                    result = run("service newrelic-daemon status", pty=False, shell=False, warn_only=True, timeout=2)
                    print bcolors.ENDC
                if re.search("running", result):
                    print bcolors.GREEN + result + bcolors.ENDC
                    inst_dict[k].append('running')
                elif re.search("stop", result):
                    print bcolors.RED + result + bcolors.ENDC
                    inst_dict[k].append('stop')
            except: Exception
    # check elb
    my_inst_id = []
    cur_elb = current_elb()
    inst_health = cur_elb.get_instance_health()
    # on recupere le status instance derriere elb
    for i in inst_health:
        if re.search("InService", str(i)):
            my_inst_id.append(i.instance_id)
    # check status instance
    for k, v in inst_dict.iteritems():
        if k in my_inst_id:
            inst_dict[k].append('inservice')
        else:
            inst_dict[k].append('outofservice')
    return inst_dict


def list_ip_tag_nrelic():
    inst_id_ip = {}
    for instance in my_instances:
        try:
            if instance.instances[0].tags['newrelic'] == "1":
                print instance.instances[0].id
                inst_id = instance.instances[0].id
                inst_ip = instance.instances[0].private_ip_address
                inst_id_ip[inst_id] = inst_ip
        except: KeyError
    return inst_id_ip


# renvoie la liste des instances demarrees avec tag newrelic
def inst_in_service():
    inst_id = []
    cur_elb = current_elb()
    inst_health = cur_elb.get_instance_health()
    for i in inst_health:
        if re.search("InService", str(i)):
            inst_id.append(i.instance_id)

# prend un instance id en parametre
def add_tag_nrelic_on(instance_id):
    for inst in my_instances:
        print inst
        if inst.instances[0].id == instance_id:
            inst.instances[0].add_tag("newrelic", "1")

def add_tag_nrelic_off(instance_id):
    for inst in my_instances:
        print inst
        if inst.instances[0].id == instance_id:
            inst.instances[0].add_tag("newrelic", "0")

def count_nrelic():
    print bcolors.YELLOW + "#################################" + bcolors.ENDC
    i = 0
    for k, v in inst_dict.iteritems():
        if v[3] == 'running':
            i += 1
    return i

def inst_to_stop(current, desire):
    time_tuples = []
    result = []
    for k, v in inst_dict.iteritems():
        if inst_dict[k][3] == 'running' and inst_dict[k][4] == 'inservice':
            time_tuples.append(tuple(inst_dict[k]))
    sorted_tuples = sorted(time_tuples, key=lambda time: time[1])
    for i in range(len(sorted_tuples)):
        if i >= desire:
            result.append(sorted_tuples[i][0])
    return result

def inst_to_start(current, desire):
    time_tuples = []
    result = []
    nbr_to_start = desire - current
    for k, v in inst_dict.iteritems():
        if inst_dict[k][3] == 'stop' and inst_dict[k][4] == 'inservice':
            time_tuples.append(tuple(inst_dict[k]))
    sorted_tuples = sorted(time_tuples, key=lambda time: time[1])
    for i in range(nbr_to_start):
        result.append(sorted_tuples[i][0])
    return result


def nrelic_stop(list_ip):
    for ip in list_ip:
        cmd = 'root@' + ip
        with settings(host_string=cmd):
            try:
                with hide('warnings'):
                    print bcolors.BLUE
                    result = run("service newrelic-daemon stop", pty=False, shell=False, warn_only=True, timeout=2)
                    print bcolors.ENDC
            except: Exception


def nrelic_start(list_ip):
    for ip in list_ip:
        cmd = 'root@' + ip
        with settings(host_string=cmd):
            try:
                with hide('warnings'):
                    print bcolors.BLUE
                    result = run("service httpd restart", pty=False, shell=False, warn_only=True, timeout=2)
                    print bcolors.ENDC
            except: Exception

def main():
    list_ip_all_instance()
    new_relic_nbr = count_nrelic()
    if new_relic_nbr > desired_nrelic_nbr:
        print bcolors.RED + '#### ' + str(new_relic_nbr) + ' agents newrelic installes ####' + bcolors.ENDC
    else:
        print bcolors.GREEN + '#### ' + str(new_relic_nbr) + ' agents newrelic installes ####' + bcolors.ENDC

    if new_relic_nbr > desired_nrelic_nbr:
        ip_inst_to_stop = inst_to_stop(new_relic_nbr, desired_nrelic_nbr)
        nrelic_stop(ip_inst_to_stop)
        print bcolors.GREEN + '#### ' + str(desired_nrelic_nbr) + ' agents newrelic demarres ####' +bcolors.ENDC
    elif new_relic_nbr < desired_nrelic_nbr:
        ip_inst_to_start = inst_to_start(new_relic_nbr, desired_nrelic_nbr)
        nrelic_start(ip_inst_to_start)


if __name__ == "__main__":
    main()
