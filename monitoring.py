#!/usr/bin/env python

#monitors the CPU and memory usage of a given application

import os
import sys
sys.path.append("../../")
import time
import threading
import subprocess
import mesos
import mesos_pb2

MAX_CPU_PER=.75
MAX_MEM_PER=.75
MIN_CPU_PER=.05
MIN_MEM_PER=.05
MAX_EXCEEDED_TIME=12 #in units of 5 seconds
MAX_UNDERUSED_TIME=24
class CPUMemMonitor():
        def __init__(self):
                self.allocatedMem=0
                self.allocatedCPU=0
                self.timeSinceExceeded=0;
                self.timeSinceUnder=0;
                self.lock=threading.RLock();
                self.stats={}
        #should be called by application whenever additional memory is allocated for removed
        def updateAllocatedMemory(self, mem, state):
                if(state=='add'):
                        self.allocatedMem=self.allocatedMem+mem
                if(state=='remove'):
                        self.allocatedMem=self.allocatedMem-mem
                if(self.allocatedMem<0):
                        print "ERROR: removing too much memory!"
                print "current Memory is: %d" % self.allocatedMem
        #called by application whenever cpus are allocated or removed
        def updateAllocatedCPU(self, cpu, state):
                if(state=='add'):
                        self.allocatedCPU=self.allocatedCPU+cpu
                if(state=='remove'):
                        self.allocatedCPU=self.allocatedCPU-cpu
                if(self.allocatedCPU<0):
                        print "ERROR: removing too much cpu!"
                print "Current CPU is: %d" % self.allocatedCPU
        def setServerCpuMem(self, slave_id, message):
                self.lock.acquire()
                self.stats[slave_id]=message
                self.lock.release()
        def monitorCPUMem(self, sched):
                while(True):
                        time.sleep(5)
                        if(self.allocatedMem==0 or self.allocatedCPU==0):
                                continue
                        if(self.timeSinceExceeded>MAX_EXCEEDED_TIME):
                                sched.scaleUp()
                                timeSinceExceeded=0
                        if(self.timeSinceUnder>MAX_UNDERUSED_TIME and self.allocatedCPU>1):
                                sched.scaleDown()
                                timeSinceUnder=0
                        cpu=0
                        mem=0
                        for slave_id in sched.slave_ids:
                                while( (not slave_id in self.stats.keys()) or self.stats[(slave_id)]==0):
                                        time.sleep(1)
                                self.lock.acquire()
                                stat=self.stats[slave_id]
                                self.stats[slave_id]=0
                                self.lock.release()
                                stat=stat.split()

                                cpu=cpu+ float(stat[0])
                                mem=mem+ float(stat[1])
                        if((cpu> self.allocatedCPU*MAX_CPU_PER) or (mem>self.allocatedMem*MAX_MEM_PER)):
                                self.timeSinceExceeded=self.timeSinceExceeded+1
                        else:
                                self.timeSinceExceeded=0
                        if((cpu<self.allocatedCPU*MIN_CPU_PER) and (mem<self.allocatedMem*MIN_MEM_PER)):
                                self.timeSinceUnder=self.timeSinceUnder+1
                        else:
                                self.timeSinceUnder=0
                        print "time since exceeded = " + str(self.timeSinceExceeded)
                        print "time since under utilized = " + str(self.timeSinceUnder)
                        print "%CPU=" + str(cpu) + " and %Memory=" + str(mem)
                        print "time to sleep again"


if __name__ == '__main__':
        mon=CPUMemMonitor(8, 512)
        mon.monitorCPUMem()
