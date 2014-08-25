import os
import sys
import time

import mesos
import mesos_pb2
from autoscaling import monitoring
from optparse import OptionParser
from subprocess import *
from socket import gethostname
import httplib
import Queue
import threading
import copy

TOTAL_TASKS = 1
START_THRESHOLD = 25
KILL_THRESHOLD = 5
#HAPROXY_EXE = "/root/haproxy-1.3.20/haproxy" #EC2
HAPROXY_EXE = "/usr/local/sbin/haproxy" #Rcluster
HAPROXY_COM1= "sudo /usr/local/sbin/haproxy -f"
HAPROXY_COM2= "-D -p /var/run/haproxy.pid"
MIN_SERVERS=1
TASK_CPUS =2
TASK_MEM = 512

class TestScheduler(mesos.Scheduler):
    def __init__(self, executor):
        self.slave_ids=[]
        self.executor = executor

        self.id=0
        self.lock = threading.RLock()
        self.haproxy = -1
        self.reconfigs = 0
        self.servers = {}
        self.overloaded = False
        self.Monitor=monitoring.CPUMemMonitor()
        self.driver=""
        self.task_ids={}

    def registered(self, driver, frameworkId, masterInfo):
        print "Registered with framework ID %s" % frameworkId.value
        self.driver=driver
        self.frameworkID=frameworkId.value
    def getFrameworkName(self, driver):
         return "haproxy+apache"
    def getExecutorInfo(self, driver):
         execPath = os.path.join(os.getcwd(), "startapache.sh")
         return mesos.ExecutorInfo(execPath, "")
    #called to reconfigure haproxy to register currently running instances of apache
    def reconfigure(self):
         print "reconfiguring haproxy"
         name = "/tmp/haproxy.conf.%d" % self.reconfigs
         with open(name, 'w') as config:
                with open('haproxy.config.template', 'r') as template:
                       for line in template:
                                config.write(line)
                       for id, host in self.servers.iteritems():
                                config.write("  ")
                                config.write("server %d %s:80 check\n" % (id, host))
#         cmd = []
         cmd= ["sudo", "/etc/init.d/haproxy", "stop"]
         self.haproxy = Popen(cmd, shell = False)
         time.sleep(1)
#         if self.haproxy != -1:
#                cmd = ["sudo", 
#                     HAPROXY_EXE,
#                      #"restart",
#                     "-f",
#                       name, "-D", "-p", "/var/run/haproxy.pid",
#                       "-sf",
#                      str(self.haproxy.pid)]
#         else:
#                cmd = ["sudo", HAPROXY_EXE, #"start",
#                       "-f", name, "-D", "-p", "/var/run/haproxy.pid" 
#                       ]
         cmd=HAPROXY_COM1.split()+ [name] + HAPROXY_COM2.split()

         self.haproxy = Popen(cmd, shell = False)
         self.reconfigs += 1
    def getScalarOffer(self, offer, name):
        for resource in offer.resources:
                if resource.name==name:
                        return resource.scalar.value

    def resourceOffers(self, driver, slave_offers):
        print "Got %d resource offers" % len(slave_offers)
        self.lock.acquire()
        tasks = []
        for offer in slave_offers:
                if offer.hostname in self.servers.values():
                        print "Rejecting slot on host " + offer.hostname + " because we've launched a server on that machine already."
                        print "self.servers currently looks like: " + str(self.servers)
                        driver.declineOffer(offer.id)
                elif not self.overloaded and len(self.servers) > 0:
                        print "Rejecting slot because we've launched enough tasks."
                        driver.declineOffer(offer.id)
                elif int(self.getScalarOffer(offer, 'mem')) < 1024:
                        print "Rejecting offer because it doesn't contain enough memory (it has " + getScalarOffer(offer, 'mem') + " and we need 1024mb."
                        driver.declineOffer(offer.id)
                elif int(self.getScalarOffer(offer, 'cpus')) < 1:
                        print "Rejecting offer because it doesn't contain enough CPUs."
                        driver.declineOffer(offer.id)

                else:
                        print "Offer is for " + str(self.getScalarOffer(offer, 'cpus')) + " CPUS and " + str(self.getScalarOffer(offer, 'mem')) + " MB on host " + offer.hostname
                        self.Monitor.updateAllocatedCPU(1, 'add')
                        self.Monitor.updateAllocatedMemory(1024, 'add')
                        params = {"cpus": "1", "mem": "1024"}

                        td = mesos_pb2.TaskInfo()
                        td.task_id.value=str(self.id)
                        self.task_ids[int(td.task_id.value)]=(td.task_id)
                        td.slave_id.value=offer.slave_id.value
                        td.name="server %d" % self.id
                        td.executor.MergeFrom(self.executor)

                        cpus = td.resources.add()
                        cpus.name = "cpus"
                        cpus.type = mesos_pb2.Value.SCALAR
                        cpus.scalar.value =int(params['cpus'])

                        mem = td.resources.add()
                        mem.name = "mem"
                        mem.type = mesos_pb2.Value.SCALAR
                        mem.scalar.value = int(params['mem'])

                        print "Accepting task, id=" + str(self.id) + ", params: " + params['cpus'] + " CPUS, and " + params['mem'] + " MB, on node " + offer.hostname
                        tasks.append(td)
                        self.slave_ids.append(offer.slave_id.value)
                        self.servers[self.id] = offer.hostname
                        self.id += 1
                        self.overloaded = False
                        driver.launchTasks(offer.id, tasks)
        #driver.replyToOffer(oid, tasks, {})
        print "done with resourceOffer()"
        self.lock.release()

    def statusUpdate(self, driver, status):
        print "received status update from taskID " + str(status.task_id.value) + ", with state: " + str(status.state)
        reconfigured = False
        self.lock.acquire()
        if int(status.task_id.value) in self.servers.keys():

                if status.state == mesos_pb2.TASK_STARTING:
                         print "Task " + str(status.task_id) + " reported that it is STARTING."
                         del self.servers[int(status.task_id.value)]
                         self.reconfigure()
                         reconfigured = True
                if status.state == mesos_pb2.TASK_RUNNING:
                         print "Task " + str(status.task_id) + " reported that it is RUNNING, reconfiguring haproxy to include it in webfarm now."
                         self.reconfigure()
                         reconfigured = True
                if status.state == mesos_pb2.TASK_FINISHED:
                         del self.servers[int(status.task_id.value)]
                         self.Monitor.updateAllocatedCPU(1, 'remove')
                         self.Monitor.updateAllocatedMemory(1024, 'remove')
                         print "Task " + str(status.task_id) + " reported FINISHED (state " + str(status.state) + ")."
                         self.reconfigure()
                         reconfigured = True
                if status.state == mesos_pb2.TASK_FAILED:
                         print "Task " + str(status.task_id) + " reported that it FAILED!"
                         self.Monitor.updateAllocatedCPU(1, 'remove')
                         self.Monitor.updateAllocatedMemory(1024, 'remove')
                         del self.servers[int(status.task_id.value)]
                         self.reconfigure()
                         reconfigured = True
                if status.state == mesos_pb2.TASK_KILLED:
                         print "Task " + str(status.task_id) + " reported that it was KILLED!"
                         self.Monitor.updateAllocatedCPU(1, 'remove')
                         self.Monitor.updateAllocatedMemory(1024, 'remove')
                         del self.servers[int(status.task_id.value)]
                         self.reconfigure()
                         reconfigured = True
                if status.state == mesos_pb2.TASK_LOST:
                         print "Task " + str(status.task_id) + " reported was LOST!"
                         self.Monitor.updateAllocatedCPU(1, 'remove')
                         self.Monitor.updateAllocatedMemory(1024, 'remove')
                         del self.servers[int(status.task_id.value)]
                         self.reconfigure()
                         reconfigured = True
        self.lock.release()
        if reconfigured:
                None#driver.reviveOffers()
        print "done in statusupdate"

    def frameworkMessage(self, driver, executorId, slaveId, message):
        self.Monitor.setServerCpuMem((slaveId.value), message)
        # The message bounced back as expected.
    def scaleUp(self):
        print "SCALING UP"
        self.lock.acquire()
        self.overloaded = True
        self.lock.release()
    def scaleDown(self, id):
        print "SCALING DOWN (removing server %d)" % id
        kill = False
        self.lock.acquire()
        if self.overloaded:
                self.overloaded = False
        else:
                kill = True
        self.lock.release()
        if kill:
                tid=(self.task_ids[id])
                self.driver.killTask(tid)

def monitor(sched):
        sched.Monitor.monitorCPUMem(sched)
def monitordata(sched):
                print "in MONITOR()"
                while True:
                        time.sleep(1)
                        print "done sleeping"
                        try:

                                conn = httplib.HTTPConnection("10.79.6.70")
                                print "done creating connection"
                                conn.request("GET", "/haproxy?stats;csv")
                                res = conn.getresponse()
                                print "testing response status"
                                if (res.status != 200):
                                        print "response = %d" % res.status
                                        continue
                                else:

                                        data = res.read()
                                        lines = data.split('\n')[2:-2]

                                        data = data.split('\n')
                                        data = data[1].split(',')
                                        print "analyzed stats"
                                print "data[33] is " + data[33]
                                print "data[4] is " + data[4]
                                if int(data[33]) >= START_THRESHOLD:
                                        print "scaling up!"
                                        sched.scaleUp()
                                elif int(data[4]) <= KILL_THRESHOLD:
                                        minload, minid = (sys.maxint, 0)

                                        for l in lines:
                                                cols = l.split(',')
                                                id = int(cols[1])
                                                load = int(cols[4])
                                                if load < minload:
                                                        minload = load
                                                        minid = id

                                        if len(lines) > MIN_SERVERS and minload == 0:
                                                print "scaling down!"
                                                sched.scaleDown(minid)
                                conn.close()
                                print "closed connection"
                        except Exception, e:
                                print "exception in monitor()"
                                continue
                print "done in MONITOR()"



if __name__ == "__main__":
    if len(sys.argv) != 2:
        print "Usage: %s master" % sys.argv[0]
        sys.exit(1)
    executor = mesos_pb2.ExecutorInfo()
    executor.executor_id.value = "default"
    executor.command.value = os.path.abspath("./startapache.sh")
    executor.name = "Test Executor (Python)"


    framework = mesos_pb2.FrameworkInfo()
    framework.user = "" # Have Mesos fill in the current user.
    framework.name = "Test Framework (Python)"

    # TODO(vinod): Make checkpointing the default when it is default
    # on the slave.
    if os.getenv("MESOS_CHECKPOINT"):
        print "Enabling checkpoint for the framework"
        framework.checkpoint = True
    sched=TestScheduler(executor)
    driver = mesos.MesosSchedulerDriver(
            sched,
            framework,
            sys.argv[1])
    threading.Thread(target = monitordata, args=[sched]).start()
    status = 0 if driver.run() == mesos_pb2.DRIVER_STOPPED else 1

    # Ensure that the driver process terminates.
    driver.stop();

    sys.exit(status)
