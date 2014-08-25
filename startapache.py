#!/usr/bin/env python
import mesos
import mesos_pb2
import sys
import time
import os
import subprocess
import atexit
import threading
import posixfile
from autoscaling import monitoring
from subprocess import *
#APACHECTL = "/usr/apache2/2.2/bin/apachectl" #EC2
APACHECTL = "sudo /etc/init.d/apache2" #R Cluster

def cleanup():
        try:
                print "Cleaning Up!"
                os.waitpid(Popen(APACHECTL + " stop", shell=True).pid, 0)
        except Exception, e:
                print e
                None
class MyExecutor(mesos.Executor):
        def __init__(self):
                mesos.Executor.__init__(self)
                self.tid = -1
        def frameworkMessage(self, driver, message):
                # Send it back to the scheduler.
                driver.sendFrameworkMessage(message)
        def launchTask(self, driver, task):
                update = mesos_pb2.TaskStatus()
                update.task_id.value=task.task_id.value
                update.state=mesos_pb2.TASK_RUNNING
                driver.sendStatusUpdate(update)
                self.tid = task.task_id
                Popen(APACHECTL + " start", shell=True)
#               threading.Thread(target = self.printStats, args=[task, driver, ]).start()

        def printStats(self, task, driver):
                while(True):
                        time.sleep(5);
                        pid=os.getpid()
                        args="ps h -p " + str(pid) + " -o %cpu,%mem"
                        args=args.split()
                        p=subprocess.Popen(args, stdout=subprocess.PIPE)
                        output=p.communicate()
                        output=output[0].split()
                        cpu=output[0]
                        mem=output[1]
                        driver.sendFrameworkMessage(cpu + " " +   mem)
        def killTask(self, driver, tid):
                print "killingTask"
                if (tid != self.tid):
                        print "Expecting different task id ... killing anyway!"
                cleanup()
                update = mesos_pb2.TaskStatus()
                update.task_id.value=tid.value
                update.state=mesos_pb2.TASK_KILLED

                driver.sendStatusUpdate(update)
        def shutdown(driver, self):
                cleanup()
        def error(self, code, message):
                print "Error: %s" % message

if __name__ == "__main__":
        print "Starting haproxy+apache executor"
        atexit.register(cleanup)
        executor = MyExecutor()
        mesos.MesosExecutorDriver(executor).run()
