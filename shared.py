#!/usr/bin/python
#
#  shared.py - common functions, classes, and variables for Vcycle
#
#  Andrew McNab, Raoul Hidalgo Charman,
#  University of Manchester.
#  Copyright (c) 2013-20. All rights reserved.
#
#  Redistribution and use in source and binary forms, with or
#  without modification, are permitted provided that the following
#  conditions are met:
#
#    o Redistributions of source code must retain the above
#      copyright notice, this list of conditions and the following
#      disclaimer.
#    o Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials
#      provided with the distribution.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND
#  CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
#  INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
#  MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
#  DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS
#  BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
#  EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
#  TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
#  DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
#  ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
#  OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
#  OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
#  POSSIBILITY OF SUCH DAMAGE.
#
#  Contacts: Andrew.McNab@cern.ch  http://www.gridpp.ac.uk/vcycle/
#

import pprint

import os
import re
import sys
import stat
import glob
import time
import json
import socket
import shutil
import string
import pycurl
import urllib
import random
import base64
import datetime
import StringIO
import tempfile
import calendar
import collections
import ConfigParser
import xml.etree.cElementTree

import vcycle.vacutils

class VcycleError(Exception):
  pass

vcycleVersion       = None
vacQueryVersion     = '01.02'	# Has to match shared.py in Vac
spaces              = None
maxWallclockSeconds = 0
curlTimeOutSeconds  = 90
takeSeconds         = 3600	# Take machines abandoned by their manager for 1.00-1.99 hours

class MachineState:
  #
  # not listed -> starting
  # starting   -> failed or running or shutdown (if we miss the time when running)
  # running    -> shutdown
  # shutdown   -> deleting
  # deleting   -> not listed or failed
  #
  # random OpenStack unreliability can require transition to failed at any time
  # stopped file created when machine first seen in shutdown, deleting, or failed state
  #
  unknown, shutdown, starting, running, deleting, failed = ('Unknown', 'Shut down', 'Starting', 'Running', 'Deleting', 'Failed')

class Machine:

  def __init__(self, name, spaceName, state, ip, createdTime, startedTime, updatedTime, uuidStr, machinetypeName, zone = None, processors = None):

    # Store values from api-specific calling function
    self.name            = name
    self.spaceName       = spaceName
    self.state           = state
    self.ip              = ip
    self.updatedTime     = updatedTime
    self.uuidStr         = uuidStr
    self.machinetypeName = machinetypeName
    self.zone            = zone

    if createdTime:
      self.createdTime  = createdTime
    else:
      try:
        # Try to recreate from created file
        self.createdTime = int(self.getFileContents('created'))
      except:
        pass

    if startedTime:
      self.startedTime = startedTime
    else:
      try:
        # Try to recreate from started file
        self.startedTime = int(self.getFileContents('started'))
      except:
        if self.state == MachineState.running:
          # If startedTime not recorded, then must just have started
          self.startedTime = int(time.time())
          self.updatedTime = self.createdTime
        else:
          self.startedTime = None

    if not self.updatedTime:
      try:
        # Try to recreate from updated file
        self.updatedTime = int(self.getFileContents('updated'))
      except:
        pass

    if not self.machinetypeName:
      # Get machinetype name saved when we requested the machine
      try:
        self.machinetypeName = self.getFileContents('machinetype_name').strip()
      except:
        pass
      else:
        if self.machinetypeName not in spaces[self.spaceName].machinetypes:
          self.machinetypeName = None

#    if not zone:
#      # Try to get zone name saved when we requested the machine
#      try:
#        f = open('/var/lib/vcycle/machines/' + name + '/zone', 'r')
#      except:
#        pass
#      else:
#        self.machinetypeName = f.read().strip()
#        f.close()

    if processors:
      self.processors = processors
    else:
      try:
        self.processors = int(self.getFileContents('jobfeatures/allocated_cpu'))
      except:
        try:
          self.processors = spaces[self.spaceName].machinetypes[self.machinetypeName].min_processors
        except:
          self.processors = 1

    try:
      self.hs06 = float(self.getFileContents('jobfeatures/hs06_job'))
      hs06Weight = self.hs06
    except:
      self.hs06 = None
      hs06Weight = float(self.processors)

    spaces[self.spaceName].totalMachines += 1
    spaces[self.spaceName].totalProcessors += self.processors

    try:
      spaces[self.spaceName].machinetypes[self.machinetypeName].totalMachines += 1
      spaces[self.spaceName].machinetypes[self.machinetypeName].totalProcessors += self.processors

      if spaces[self.spaceName].machinetypes[self.machinetypeName].target_share > 0.0:
         spaces[self.spaceName].machinetypes[self.machinetypeName].weightedMachines += (hs06Weight / spaces[self.spaceName].machinetypes[self.machinetypeName].target_share)
    except:
      pass

    if self.state == MachineState.starting:
      try:
        spaces[self.spaceName].machinetypes[self.machinetypeName].startingProcessors += self.processors
      except:
        pass

    if self.state == MachineState.running:
      try:
        if not self.startedTime:
          self.startedTime = int(time.time())
          self.updatedTime = self.startedTime

        spaces[self.spaceName].runningMachines += 1
        spaces[self.spaceName].runningProcessors += self.processors

        try:
          spaces[self.spaceName].machinetypes[self.machinetypeName].runningMachines += 1
          spaces[self.spaceName].machinetypes[self.machinetypeName].runningProcessors += self.processors
        except:
          pass

        if self.hs06 is not None:
          # We check runningHS06 first in case hs06_per_processor removed from machinetype in config
          if spaces[self.spacename].runningHS06 is not None:
            spaces[self.spacename].runningHS06 += self.hs06

          try:
            spaces[self.spaceName].machinetypes[self.machinetypeName].runningHS06 += self.hs06
          except:
            pass

      except:
        pass

    try:
      if self.state == MachineState.starting or \
         (self.state == MachineState.running and \
          ((int(time.time()) - startedTime) < spaces[self.spaceName].machinetypes[self.machinetypeName].fizzle_seconds)):
        spaces[self.spaceName].machinetypes[self.machinetypeName].notPassedFizzle += 1
    except:
      pass

    try:
      self.manager = self.getFileContents('manager')
    except:
      self.manager = None
      self.managedHere = False
    else:
      if self.manager == os.uname()[1]:
        self.managedHere = True
      else:
        self.managedHere = False

    if self.managedHere:
      self.managerHeartbeatTime = int(time.time())
      self.setFileContents('manager_heartbeat', str(self.managerHeartbeatTime))
    else:
      try:
        self.managerHeartbeatTime = int(self.getFileContents('manager_heartbeat'))
      except:
        self.managerHeartbeatTime = None

    # Record when the machine started (rather than just being created)
    if self.managedHere and self.startedTime and not os.path.isfile(self.machineDir() + '/started'):
      self.setFileContents('started', str(self.startedTime))
      self.setFileContents('updated', str(self.updatedTime))

    try:
      self.deletedTime = int(self.getFileContents('deleted'))
    except:
      self.deletedTime = None

    # Set heartbeat time if available
    self.getHeartbeatTime()

    # Check if the machine already has a stopped timestamp
    try:
      self.stoppedTime = int(self.getFileContents('stopped'))
    except:
      if self.managedHere and (self.state == MachineState.shutdown or self.state == MachineState.failed or self.state == MachineState.deleting):
        # Record that we have seen the machine in a stopped state for the first time
        # If updateTime has the last transition time, presumably it is to being stopped.
        # This is certainly a better estimate than using time.time() if available (ie OpenStack)
        if not self.updatedTime:
          self.updatedTime = int(time.time())
          self.setFileContents('updated', str(self.updatedTime))

        self.stoppedTime = self.updatedTime
        self.setFileContents('stopped', str(self.stoppedTime))

        # Record the shutdown message if available
        self.getShutdownMessage()

        if self.shutdownMessage:
          vcycle.vacutils.logLine('Machine ' + name + ' shuts down with message "' + self.shutdownMessage + '"')
          try:
            shutdownCode = int(self.shutdownMessage.split(' ')[0])
          except:
            shutdownCode = None
        else:
            shutdownCode = None

        if self.machinetypeName:
          # Store last abort time for stopped machines, based on shutdown message code
          if shutdownCode and \
             (shutdownCode >= 300) and \
             (shutdownCode <= 699) and \
             (self.stoppedTime > spaces[self.spaceName].machinetypes[self.machinetypeName].lastAbortTime):
            vcycle.vacutils.logLine('Set ' + self.spaceName + ' ' + self.machinetypeName + ' lastAbortTime ' + str(self.stoppedTime) +
                                    ' due to ' + name + ' shutdown message')
            spaces[self.spaceName].machinetypes[self.machinetypeName].setLastAbortTime(self.stoppedTime)

          elif self.startedTime and \
               (self.stoppedTime > spaces[self.spaceName].machinetypes[self.machinetypeName].lastAbortTime) and \
               ((self.stoppedTime - self.startedTime) < spaces[self.spaceName].machinetypes[self.machinetypeName].fizzle_seconds):

            # Store last abort time for stopped machines, based on fizzle_seconds
            vcycle.vacutils.logLine('Set ' + self.spaceName + ' ' + self.machinetypeName + ' lastAbortTime ' + str(self.stoppedTime) +
                                    ' due to ' + name + ' fizzle')
            spaces[self.spaceName].machinetypes[self.machinetypeName].setLastAbortTime(self.stoppedTime)

          if self.startedTime and shutdownCode and (shutdownCode / 100) == 3:
            vcycle.vacutils.logLine('For ' + self.spaceName + ':' + self.machinetypeName + ' minimum fizzle_seconds=' +
                                      str(self.stoppedTime - self.startedTime) + ' ?')

          # Machine finished messages for APEL and VacMon
          self.writeApel()
          self.sendMachineMessage()
      else:
        self.stoppedTime = None

    if self.startedTime:
      logStartedTimeStr = str(self.startedTime - self.createdTime) + 's'
    else:
      logStartedTimeStr = '-'

    if self.updatedTime:
      logUpdatedTimeStr = str(self.updatedTime - self.createdTime) + 's'
    else:
      logUpdatedTimeStr = '-'

    if self.stoppedTime:
      logStoppedTimeStr = str(self.stoppedTime - self.createdTime) + 's'
    else:
      logStoppedTimeStr = '-'

    if self.heartbeatTime:
      logHeartbeatTimeStr = str(int(time.time()) - self.heartbeatTime) + 's'
    else:
      logHeartbeatTimeStr = '-'

    vcycle.vacutils.logLine('= ' + name + ' in ' +
                            str(self.spaceName) + ':' +
                            (self.zone if self.zone else '') + ':' +
                            str(self.machinetypeName) + ' ' +
                            str(self.processors) + ' ' + self.ip + ' ' +
                            self.state + ' ' +
                            time.strftime("%b %d %H:%M:%S ", time.localtime(self.createdTime)) +
                            logStartedTimeStr + ':' +
                            logUpdatedTimeStr + ':' +
                            logStoppedTimeStr + ':' +
                            logHeartbeatTimeStr
                           )

  def machineDir(self):
    return spaces[self.spaceName].machineDir(self.name)

  def getFileContents(self, fileName):
    # Get the contents of a file for this machine
    return spaces[self.spaceName].getFileContents(self.name, fileName)

  def setFileContents(self, fileName, contents, mode = stat.S_IRUSR|stat.S_IWUSR|stat.S_IRGRP):
    # Set the contents of a file for the given machine
    spaces[self.spaceName].setFileContents(self.name, fileName, contents, mode = mode)

  def writeApel(self):

    # If the VM just ran for fizzle_seconds, then we don't log it
    try:
      if (self.stoppedTime - self.startedTime) < spaces[self.spaceName].machinetypes[self.machinetypeName].fizzle_seconds:
        return
    except:
      return

    nowTime = time.localtime()

    userDN = ''
    for component in self.spaceName.split('.'):
      userDN = '/DC=' + component + userDN

    if hasattr(spaces[self.spaceName].machinetypes[self.machinetypeName], 'accounting_fqan'):
      userFQANField = 'FQAN: ' + spaces[self.spaceName].machinetypes[self.machinetypeName].accounting_fqan + '\n'
    else:
      userFQANField = ''

    try:
      kb = int(self.getFileContents('jobfeatures/max_rss_bytes')) / 1024
    except:
      memoryField = ''
    else:
      memoryField = 'MemoryReal: '    + str(kb) + '\nMemoryVirtual: ' + str(kb) + '\n'

    try:
      processors = int(self.getFileContents('jobfeatures/allocated_cpu'))
    except:
      processorsField = ''
    else:
      processorsField = 'Processors: ' + str(processors) + '\n'

    if spaces[self.spaceName].gocdb_sitename:
      tmpGocdbSitename = spaces[self.spaceName].gocdb_sitename
    else:
      tmpGocdbSitename = '.'.join(self.spaceName.split('.')[1:]) if '.' in self.spaceName else self.spaceName

    mesg = ('APEL-individual-job-message: v0.3\n' +
            'Site: ' + tmpGocdbSitename + '\n' +
            'SubmitHost: ' + self.spaceName + '/vcycle-' + os.uname()[1] + '\n' +
            'LocalJobId: ' + self.uuidStr + '\n' +
            'LocalUserId: ' + self.name + '\n' +
            'Queue: ' + self.machinetypeName + '\n' +
            'GlobalUserName: ' + userDN + '\n' +
            userFQANField +
            'WallDuration: ' + str(self.stoppedTime - self.startedTime) + '\n' +
            # Can we do better for CpuDuration???
            'CpuDuration: ' + str(self.stoppedTime - self.startedTime) + '\n' +
            processorsField +
            'NodeCount: 1\n' +
            'InfrastructureDescription: APEL-VCYCLE\n' +
            'InfrastructureType: grid\n' +
            'StartTime: ' + str(self.startedTime) + '\n' +
            'EndTime: ' + str(self.stoppedTime) + '\n' +
            memoryField +
            'ServiceLevelType: HEPSPEC\n' +
            'ServiceLevel: ' + str(self.hs06 if self.hs06 else 1.0) + '\n' +
            '%%\n')

    fileName = time.strftime('%H%M%S', nowTime) + str(time.time() % 1)[2:][:8]

    try:
      os.makedirs(time.strftime('/var/lib/vcycle/apel-archive/%Y%m%d', nowTime), stat.S_IRUSR|stat.S_IWUSR|stat.S_IXUSR|stat.S_IRGRP|stat.S_IXGRP|stat.S_IROTH|stat.S_IXOTH)
    except:
      pass

    try:
      vcycle.vacutils.createFile(time.strftime('/var/lib/vcycle/apel-archive/%Y%m%d/', nowTime) + fileName, mesg, stat.S_IRUSR|stat.S_IWUSR|stat.S_IRGRP|stat.S_IROTH, '/var/lib/vcycle/tmp')
    except:
      vcycle.vacutils.logLine('Failed creating ' + time.strftime('/var/lib/vcycle/apel-archive/%Y%m%d/', nowTime) + fileName)
      return

    if spaces[self.spaceName].gocdb_sitename:
      # We only write to apel-outgoing if gocdb_sitename is set
      try:
        os.makedirs(time.strftime('/var/lib/vcycle/apel-outgoing/%Y%m%d', nowTime), stat.S_IRUSR|stat.S_IWUSR|stat.S_IXUSR|stat.S_IRGRP|stat.S_IXGRP|stat.S_IROTH|stat.S_IXOTH)
      except:
        pass

      try:
        vcycle.vacutils.createFile(time.strftime('/var/lib/vcycle/apel-outgoing/%Y%m%d/', nowTime) + fileName, mesg, stat.S_IRUSR|stat.S_IWUSR|stat.S_IRGRP|stat.S_IROTH, '/var/lib/vcycle/tmp')
      except:
        vcycle.vacutils.logLine('Failed creating ' + time.strftime('/var/lib/vcycle/apel-outgoing/%Y%m%d/', nowTime) + fileName)
        return

  def sendMachineMessage(self, cookie = '0'):
    if not spaces[self.spaceName].vacmons:
      return

    timeNow = int(time.time())

    if spaces[self.spaceName].gocdb_sitename:
      tmpGocdbSitename = spaces[self.spaceName].gocdb_sitename
    else:
      tmpGocdbSitename = '.'.join(self.spaceName.split('.')[1:]) if '.' in self.spaceName else self.spaceName

    if not self.startedTime:
      cpuSeconds = 0
    elif self.stoppedTime:
      cpuSeconds = self.stoppedTime - self.startedTime
    elif self.state == MachineState.running:
      cpuSeconds = timeNow - self.startedTime
    else:
      cpuSeconds = 0

    messageDict = {
                'message_type'          : 'machine_status',
                'daemon_version'        : 'Vcycle ' + vcycleVersion + ' vcycled',
                'vacquery_version'      : 'VacQuery ' + vacQueryVersion,
                'cookie'                : cookie,
                'space'                 : self.spaceName,
                'site'                  : tmpGocdbSitename,
                'factory'               : os.uname()[1],
                'num_machines'          : 1,
                'time_sent'             : timeNow,

                'machine'               : self.name,
                'state'                 : self.state,
                'uuid'                  : self.uuidStr,
                'created_time'          : self.createdTime,
                'started_time'          : self.startedTime,
                'heartbeat_time'        : self.heartbeatTime,
                'num_processors'        : self.processors,
                'cpu_seconds'           : cpuSeconds,
                'cpu_percentage'        : 100.0,
                'machinetype'           : self.machinetypeName
                   }

    if self.hs06:
      messageDict['hs06'] = self.hs06

    try:
      messageDict['fqan'] = spaces[self.spaceName].machinetypes[machinetypeName].accounting_fqan
    except:
      pass

    try:
      messageDict['shutdown_message'] = self.shutdownMessage
    except:
      pass

    try:
      messageDict['shutdown_time'] = self.shutdownMessageTime
    except:
      pass

    messageJSON = json.dumps(messageDict)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    for vacmonHostPort in spaces[self.spaceName].vacmons:
      (vacmonHost, vacmonPort) = vacmonHostPort.split(':')

      vcycle.vacutils.logLine('Sending VacMon machine finished message to %s:%s' % (vacmonHost, vacmonPort))

      sock.sendto(messageJSON, (vacmonHost,int(vacmonPort)))

    sock.close()

  def getShutdownMessage(self):

     try:
       self.shutdownMessage = self.getFileContents('joboutputs/shutdown_message').strip()
       self.shutdownMessageTime = int(os.stat(self.machineDir() + '/joboutputs/shutdown_message').st_ctime)
     except:
       self.shutdownMessage     = None
       self.shutdownMessageTime = None

  def getHeartbeatTime(self):

     # No valid machinetype (probably removed from configuration)
     if not self.machinetypeName:
       self.heartbeatTime = None
       return

     try:
       self.heartbeatTime = int(os.stat(self.machineDir() + '/joboutputs/' + spaces[self.spaceName].machinetypes[self.machinetypeName].heartbeat_file).st_ctime)
     except:
       self.heartbeatTime = None

class Machinetype:

  def __init__(self, spaceName, spaceFlavorNames, machinetypeName, parser, machinetypeSectionName):

    global maxWallclockSeconds

    self.spaceName  = spaceName
    self.machinetypeName = machinetypeName

    # Recreate lastAbortTime (must be set/updated with setLastAbortTime() to create file)
    try:
      f = open('/var/lib/vcycle/shared/last_abort_times/' + self.spaceName + '/' + self.machinetypeName, 'r')
    except:
      self.lastAbortTime = 0
    else:
      self.lastAbortTime = int(f.read().strip())
      f.close()

    # Always set machinetype_path, saved in vacuum pipe processing or default using machinetype name
    try:
      self.machinetype_path = parser.get(machinetypeSectionName, 'machinetype_path')
    except:
      self.machinetype_path = '/var/lib/vcycle/spaces/' + self.spaceName + '/machinetypes/' +  self.machinetypeName

    try:
      self.root_image = parser.get(machinetypeSectionName, 'root_image')
    except:
      self.root_image = None

    try:
      self.cernvm_signing_dn = parser.get(machinetypeSectionName, 'cernvm_signing_dn')
    except:
      self.cernvm_signing_dn = None

    if parser.has_option(machinetypeSectionName, 'flavor_name'):
      vcycle.vacutils.logLine('Option flavor_name is deprecated, please use flavor_names!')
      try:
        self.flavor_names = parser.get(machinetypeSectionName, 'flavor_name').strip().split()
      except:
        self.flavor_names = spaceFlavorNames
    else:
      try:
        self.flavor_names = parser.get(machinetypeSectionName, 'flavor_names').strip().split()
      except:
        self.flavor_names = spaceFlavorNames

    try:
      self.min_processors = int(parser.get(machinetypeSectionName, 'cpu_per_machine'))
    except:
      pass
    else:
      vcycle.vacutils.logLine('cpu_per_machine is deprecated - please use min_processors')

    try:
      self.min_processors = int(parser.get(machinetypeSectionName, 'processors_per_machine'))
    except:
      pass
    else:
      vcycle.vacutils.logLine('processors_per_machine is deprecated - please use min_processors')

    try:
      self.min_processors = int(parser.get(machinetypeSectionName, 'min_processors'))
    except Exception as e:
      self.min_processors = 1

    try:
      self.max_processors = int(parser.get(machinetypeSectionName, 'max_processors'))
    except Exception as e:
      self.max_processors = None
      
    if self.max_processors is not None and self.max_processors < self.min_processors:
      raise VcycleError('max_processors cannot be less than min_processors!')
        
    try:
      self.disk_gb_per_processor = int(parser.get(machinetypeSectionName, 'disk_gb_per_processor'))
    except Exception as e:
      self.disk_gb_per_processor = None

    try:
      self.root_public_key = parser.get(machinetypeSectionName, 'root_public_key')
    except:
      self.root_public_key = '/root/.ssh/id_rsa.pub'
      
      if not os.path.exists(self.root_public_key):
        self.root_public_key = None

    try:
      if parser.has_option(machinetypeSectionName, 'processors_limit'):
        self.processors_limit = int(parser.get(machinetypeSectionName, 'processors_limit'))
      else:
        self.processors_limit = None
    except Exception as e:
      raise VcycleError('Failed to parse processors_limit in [' + machinetypeSectionName + '] (' + str(e) + ')')

    if parser.has_option(machinetypeSectionName, 'max_starting_processors'):
      try:
        self.max_starting_processors = int(parser.get(machinetypeSectionName, 'max_starting_processors'))
      except Exception as e:
        raise VcycleError('Failed to parse max_starting_processors in [' + machinetypeSectionName + '] (' + str(e) + ')')
    else:
      self.max_starting_processors = self.processors_limit

    try:
      self.backoff_seconds = int(parser.get(machinetypeSectionName, 'backoff_seconds'))
    except Exception as e:
      raise VcycleError('backoff_seconds is required in [' + machinetypeSectionName + '] (' + str(e) + ')')

    try:
      self.fizzle_seconds = int(parser.get(machinetypeSectionName, 'fizzle_seconds'))
    except Exception as e:
      raise VcycleError('fizzle_seconds is required in [' + machinetypeSectionName + '] (' + str(e) + ')')

    try:
      if parser.has_option(machinetypeSectionName, 'max_wallclock_seconds'):
        self.max_wallclock_seconds = int(parser.get(machinetypeSectionName, 'max_wallclock_seconds'))
      else:
        self.max_wallclock_seconds = 86400

      if self.max_wallclock_seconds > maxWallclockSeconds:
        maxWallclockSeconds = self.max_wallclock_seconds
    except Exception as e:
      raise VcycleError('max_wallclock_seconds is required in [' + machinetypeSectionName + '] (' + str(e) + ')')


    if parser.has_option(machinetypeSectionName, 'x509dn'):
      vcycle.vacutils.logLine('x509dn in [' + machinetypeSectionName + '] is deprecated - please use https_x509dn')
      self.https_x509dn = parser.get(machinetypeSectionName, 'x509dn')
    else:
      try:
        self.https_x509dn = parser.get(machinetypeSectionName, 'https_x509dn')
      except:
        self.https_x509dn = None

# The heartbeat and joboutputs options should cause errors if x509dn isn't given!

    try:
      self.heartbeat_file = parser.get(machinetypeSectionName, 'heartbeat_file')
    except:
      self.heartbeat_file = None

    try:
      if parser.has_option(machinetypeSectionName, 'heartbeat_seconds'):
        self.heartbeat_seconds = int(parser.get(machinetypeSectionName, 'heartbeat_seconds'))
      else:
        self.heartbeat_seconds = None
    except Exception as e:
      raise VcycleError('Failed to parse heartbeat_seconds in [' + machinetypeSectionName + '] (' + str(e) + ')')

    try:
      s = parser.get(machinetypeSectionName, 'cvmfs_proxy_machinetype')
    except:
      self.cvmfsProxyMachinetype     = None
      self.cvmfsProxyMachinetypePort = None
    else:
      if ':' in s:
        try:
          self.cvmfsProxyMachinetype = s.split(':')[0]
          self.cvmfsProxyMachinetypePort = int(s.split(':')[1])
        except: 
          raise VcycleError('Failed to parse cmvfs_proxy_machinetype = ' + s + ' in [' + machinetypeSectionName + '] (' + str(e) + ')')
      else:
        self.cvmfsProxyMachinetype     = s
        self.cvmfsProxyMachinetypePort = 280


    # All the options for saving joboutputs and remote joboutputs are deprecated now 
    # remote volumes can be used for /var/lib/vcycle/shared/machines/SPACENAME/deleted

    if parser.has_option(machinetypeSectionName, 'log_joboutputs'):
      vcycle.vacutils.logLine('log_joboutputs is deprecated.')

    if parser.has_option(machinetypeSectionName, 'log_machineoutputs'):
      vcycle.vacutils.logLine('log_machineoutputs is deprecated.')

    if parser.has_option(machinetypeSectionName, 'machineoutputs_days'):
      vcycle.vacutils.logLine('machineoutputs_days is deprecated.')

    if parser.has_option(machinetypeSectionName, 'joboutputs_days'):
      vcycle.vacutils.logLine('joboutputs_days is deprecated.')
    
    if parser.has_option(machinetypeSectionName, 'remote_joboutputs_url'):
      vcycle.vacutils.logLine('remote_joboutputs_url is deprecated.')

    if parser.has_option(machinetypeSectionName, 'remote_joboutputs_cert'):
      vcycle.vacutils.logLine('remote_joboutputs_cert is deprecated.')

    if parser.has_option(machinetypeSectionName, 'remote_joboutputs_key'):
      vcycle.vacutils.logLine('remote_joboutputs_key is deprecated.')
    
    
    if parser.has_option(machinetypeSectionName, 'accounting_fqan'):
      self.accounting_fqan = parser.get(machinetypeSectionName, 'accounting_fqan')

    try:
      self.rss_bytes_per_processor = 1048576 * int(parser.get(machinetypeSectionName, 'mb_per_processor'))
    except:
      # If not set explicitly, defaults to 2048 MB per processor
      self.rss_bytes_per_processor = 2147483648

    if parser.has_option(machinetypeSectionName, 'hs06_per_processor'):
      try:
        self.hs06_per_processor = float(parser.get(machinetypeSectionName, 'hs06_per_processor'))
      except Exception as e:
        VcycleError('Failed to parse hs06_per_processor in [' + machinetypeSectionName + '] (' + str(e) + ')')
      else:
        self.runningHS06 = 0.0
    else:
      self.hs06_per_processor = None
      self.runningHS06 = None

    try:
      self.user_data = parser.get(machinetypeSectionName, 'user_data')
    except Exception as e:
      raise VcycleError('user_data is required in [' + machinetypeSectionName + '] (' + str(e) + ')')

    try:
      if parser.has_option(machinetypeSectionName, 'target_share'):
        self.target_share = float(parser.get(machinetypeSectionName, 'target_share'))
      else:
        self.target_share = 0.0
    except Exception as e:
      raise VcycleError('Failed to parse target_share in [' + machinetypeSectionName + '] (' + str(e) + ')')

    # self.options will be passed to vacutils.createUserData()
    self.options = {}

    for (oneOption, oneValue) in parser.items(machinetypeSectionName):
      if (oneOption[0:17] == 'user_data_option_') or (oneOption[0:15] == 'user_data_file_'):
        if string.translate(oneOption, None, '0123456789abcdefghijklmnopqrstuvwxyz_') != '':
          raise VcycleError('Name of user_data_xxx (' + oneOption + ') must only contain a-z 0-9 and _')
        else:
          self.options[oneOption] = oneValue

    if parser.has_option(machinetypeSectionName, 'user_data_proxy_cert') or \
       parser.has_option(machinetypeSectionName, 'user_data_proxy_key') :
      vcycle.vacutils.logLine('user_data_proxy_cert and user_data_proxy_key are deprecated. Please use user_data_proxy = True and create x509cert.pem and x509key.pem!')

    if parser.has_option(machinetypeSectionName, 'user_data_proxy') and \
       parser.get(machinetypeSectionName,'user_data_proxy').lower() == 'true':
      self.options['user_data_proxy'] = True
    else:
      self.options['user_data_proxy'] = False    

    if parser.has_option(machinetypeSectionName, 'legacy_proxy') and \
       parser.get(machinetypeSectionName, 'legacy_proxy').lower() == 'true':
      self.options['legacy_proxy'] = True
    else:
      self.options['legacy_proxy'] = False
    
    # Just for this instance, so Total for this machinetype in one space
    self.totalMachines      = 0
    self.totalProcessors    = 0
    self.startingProcessors = 0
    self.runningMachines    = 0
    self.runningProcessors  = 0
    self.weightedMachines   = 0.0
    self.notPassedFizzle    = 0

  def setLastAbortTime(self, abortTime):

    if abortTime > self.lastAbortTime:
      self.lastAbortTime = abortTime

      try:
        os.makedirs('/var/lib/vcycle/shared/last_abort_times/' + self.spaceName,
                    stat.S_IWUSR + stat.S_IXUSR + stat.S_IRUSR + stat.S_IXGRP + stat.S_IRGRP + stat.S_IXOTH + stat.S_IROTH)
      except:
        pass

      vcycle.vacutils.createFile('/var/lib/vcycle/shared/last_abort_times/' + self.spaceName + '/' + self.machinetypeName,
                                 str(abortTime), tmpDir = '/var/lib/vcycle/shared/tmp')

  def makeMachineName(self):
    """Construct a machine name including the machinetype"""

    while True:
      machineName = 'vcycle-' + self.machinetypeName + '-' + ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))      

      if not os.path.exists(spaces[self.spaceName].machineDir(machineName)):
        break
  
      vcycle.vacutils.logLine('New random machine name ' + machineName + ' already exists! Trying another name ...')

    return machineName

class BaseSpace(object):

  def __init__(self, api, apiVersion, spaceName, parser, spaceSectionName, updatePipes):
    self.api        = api
    self.apiVersion = apiVersion
    self.spaceName  = spaceName

    self.processors_limit   = None
    self.totalMachines      = 0
    # totalProcessors includes ones Vcycle doesn't manage
    self.totalProcessors    = 0
    self.runningMachines    = 0
    self.runningProcessors  = 0
    self.runningHS06        = None
    self.zones              = None
    self.maxStartingSeconds = 3600
    self.shutdownTime  = None

    if parser.has_option(spaceSectionName, 'max_processors'):
      vcycle.vacutils.logLine('max_processors (in space ' + spaceName + ') is deprecated - please use processors_limit')
      try:
        self.processors_limit = int(parser.get(spaceSectionName, 'max_processors'))
      except:
        raise VcycleError('Failed to parse max_processors in [space ' + spaceName + '] (' + str(e) + ')')
      
    elif parser.has_option(spaceSectionName, 'processors_limit'):
      try:
        self.processors_limit = int(parser.get(spaceSectionName, 'processors_limit'))
      except Exception as e:
        raise VcycleError('Failed to parse processors_limit in [space ' + spaceName + '] (' + str(e) + ')')

    try:
      self.flavor_names = parser.get(spaceSectionName, 'flavor_names').strip().split()
    except:
      self.flavor_names = []
      
    try:
      self.volume_gb_per_processor = int(parser.get(spaceSectionName, 'volume_gb_per_processor'))
    except:
      self.volume_gb_per_processor = 0

    if parser.has_option(spaceSectionName, 'shutdown_time'):
      try:
        self.shutdownTime = int(parser.get(spaceSectionName,
          'shutdown_time'))
      except Exception as e:
        raise VcycleError('Failed to check parse shutdown_time in ['
            + spaceSectionName + '] (' + str(e) + ')')

    # First go through the vacuum_pipe sections for this space, creating
    # machinetype sections in the configuration on the fly
    for vacuumPipeSectionName in parser.sections():
      try:
        (sectionType, spaceTemp, machinetypeNamePrefix) = vacuumPipeSectionName.lower().split(None,2)
      except:
        continue

      if spaceTemp != spaceName or sectionType != 'vacuum_pipe':
        continue

      try:
        self._expandVacuumPipe(parser, vacuumPipeSectionName, machinetypeNamePrefix, updatePipes)
      except Exception as e:
        raise VcycleError('Failed expanding vacuum pipe [' + vacuumPipeSectionName + ']: ' + str(e))

    # Now go through the machinetypes for this space in the configuration,
    # possibly including ones created from vacuum pipes
    self.machinetypes = {}

    for machinetypeSectionName in parser.sections():
      try:
        (sectionType, spaceTemp, machinetypeName) = machinetypeSectionName.lower().split(None,2)
      except:
        continue

      if sectionType != 'machinetype' or spaceTemp != spaceName:
        continue

      if string.translate(machinetypeName, None, '0123456789abcdefghijklmnopqrstuvwxyz-') != '':
        raise VcycleError('Name of machinetype in [machinetype ' + spaceName + ' ' + machinetypeName + '] can only contain a-z 0-9 or -')

      try:
        self.machinetypes[machinetypeName] = Machinetype(spaceName, self.flavor_names, machinetypeName, parser, machinetypeSectionName)
      except Exception as e:
        raise VcycleError('Failed to initialize [machinetype ' + spaceName + ' ' + machinetypeName + '] (' + str(e) + ')')

      if self.runningHS06 is None and self.machinetypes[machinetypeName].hs06_per_processor is not None:
        self.runningHS06 = 0.0

    if len(self.machinetypes) < 1:
      raise VcycleError('No machinetypes defined for space ' + spaceName + ' - each space must have at least one machinetype!')

    # Start new curl session for this instance
    self.curl  = pycurl.Curl()
    self.token = None

    # Dictionary of all the Vcycle-created VMs in this space: None in case failed to connect and do scan successfully
    self.machines = None
    
    # Dictionary of all the Vcycle-created volumes in this space
    self.volumes = None

  def _expandVacuumPipe(self, parser, vacuumPipeSectionName, machinetypeNamePrefix, updatePipes):
    """ Read configuration settings from a vacuum pipe """

    acceptedOptions = [
        'accounting_fqan',
        'backoff_seconds',
        'cache_seconds',
        'cvmfs_repositories',
        'fizzle_seconds',
        'heartbeat_file',
        'heartbeat_seconds',
        'image_signing_dn',
        'legacy_proxy',
        'machine_model',
        'max_processors',
        'max_wallclock_seconds',
        'min_processors',
        'min_wallclock_seconds',
        'root_device',
        'root_image',
        'scratch_device',
        'suffix',
        'target_share',
        'user_data',
        'user_data_proxy'
        ]

    try:
      vacuumPipeURL = parser.get(vacuumPipeSectionName, 'vacuum_pipe_url')
    except:
      raise VcycleError('Section vacuum_pipe ' + machinetypeNamePrefix + ' in space ' + spaceName + ' has no vacuum_pipe_url option!')

    # This is the total in the local configuation, for this pipe and its machinetypes
    try:
      totalTargetShare = float(parser.get(vacuumPipeSectionName, 'target_share').strip())
    except:
      totalTargetShare = 0.0

    try:
      vacuumPipe = vcycle.vacutils.readPipe('/var/lib/vcycle/pipescache',
                                            vacuumPipeURL,
                                            'vcycle ' + vcycleVersion,
                                            updatePipes = updatePipes)
    except Exception as e:
      raise VcycleError(vacuumPipeURL + ' given but failed reading/updating the pipe: ' + str(e))

    # This is the total in the remote pipe file, for the machinetypes it defines
    totalPipeTargetShare = 0.0
              
    # First pass to get total target shares in the remote vacuum pipe
    for pipeMachinetype in vacuumPipe['machinetypes']:
      try:
        totalPipeTargetShare += float(pipeMachinetype['target_share'])
      except:
        pass

    # Second pass to add options to the relevant machinetype sections
    for pipeMachinetype in vacuumPipe['machinetypes']:
    
      if 'machine_model' in pipeMachinetype and str(pipeMachinetype['machine_model']) not in ['cernvm3','vm-raw']:
        vcycle.vacutils.logLine("Not a supported machine_model: %s - skipping!" % str(pipeMachinetype['machine_model']))
        continue    

      try:
        suffix = str(pipeMachinetype['suffix'])
      except:
        vcycle.vacutils.logLine("suffix is missing from one machinetype within " + vacuumPipeURL + " - skipping!")
        continue
                
      try:
        parser.add_section('machinetype ' + self.spaceName + ' ' + machinetypeNamePrefix + '-' + suffix)
      except:
        # Ok if it already exists
        pass

      # Copy almost all options from vacuum_pipe section to this new machinetype
      # unless they have already been given. Skip vacuum_pipe_url and target_share                  
      for n,v in parser.items(vacuumPipeSectionName):
        if n != 'vacuum_pipe_url' and n != 'target_share' and \
           not parser.has_option('machinetype ' + self.spaceName + ' ' + machinetypeNamePrefix + '-' + suffix, n):
          parser.set('machinetype ' + self.spaceName + ' ' + machinetypeNamePrefix + '-' + suffix, n, v)

      # Record path to machinetype used to find the files on local disk
      parser.set('machinetype ' + self.spaceName + ' ' + machinetypeNamePrefix + '-' + suffix,
                 'machinetype_path', '/var/lib/vcycle/spaces/' + self.spaceName + '/machinetypes/' +  machinetypeNamePrefix)      

      # Go through vacuumPipe adding options if not already present from configuration files
      for optionRaw in pipeMachinetype:
        option = str(optionRaw)
        value  = str(pipeMachinetype[optionRaw])

        # Skip if option already exists for this machinetype - configuration 
        # file sections take precedence
        if parser.has_option('machinetype ' + self.spaceName + ' ' + machinetypeNamePrefix + '-' + suffix, option):
          continue
        
        # Deal with subdividing the total target share for this vacuum pipe here
        # Each machinetype gets a share based on its target_share within the pipe
        # We do the normalisation of the pipe target_shares here
        if option == 'target_share':
          try:
            targetShare = totalTargetShare * (float(value) / totalPipeTargetShare)
          except:
            targetShare = 0.0

          parser.set('machinetype ' + self.spaceName + ' ' + machinetypeNamePrefix + '-' + suffix, 
                     'target_share', str(targetShare))
          continue

        # Silently skip some options processed already
        if option == 'machine_model':
          continue

        # Check option is one we accept
        if not option.startswith('user_data_file_' ) and \
           not option.startswith('user_data_option_' ) and \
           not option in acceptedOptions:
          vcycle.vacutils.logLine('Option %s is not accepted from vacuum pipe - ignoring!' % option)
          continue

        # Any options which specify filenames on the hypervisor must be checked here
        if (option.startswith('user_data_file_' )  or
            option ==         'heartbeat_file'   ) and '/' in value:
          vcycle.vacutils.logLine('Option %s in %s cannot contain a "/" - ignoring!'
             % (option, vacuumPipeURL))
          continue

        elif (option == 'user_data' or option == 'root_image') and '/../' in value:
          vcycle.vacutils.logLine('Option %s in %s cannot contain "/../" - ignoring!'
             % (option, vacuumPipeURL))
          continue

        elif option == 'user_data' and '/' in value and \
           not value.startswith('http://') and \
           not value.startswith('https://'):
          vcycle.vacutils.logLine('Option %s in %s cannot contain a "/" unless http(s)://... - ignoring!'
             % (option, vacuumPipeURL))
          continue

        elif option == 'root_image' and '/' in value and \
           not value.startswith('http://') and \
           not value.startswith('https://'):
          vcycle.vacutils.logLine('Option %s in %s cannot contain a "/" unless http(s)://... - ignoring!'
             % (option, vacuumPipeURL))
          continue
          
        # if all OK, then can set value as if from configuration files
        parser.set('machinetype ' + self.spaceName + ' ' + machinetypeNamePrefix + '-' + suffix, 
                   option, value)

  def findMachinesWithFile(self, fileName):
    # Return a list of machine names that have the given fileName (only used by EC2 plugin currently)

    machineNames = []
    pathsList    = glob.glob('/var/lib/vcycle/shared/spaces/' + self.spaceName + '/current/*/' + fileName)

    if pathsList:
      for onePath in pathsList:
        machineNames.append(onePath.split('/')[8])

    return machineNames
    
  def machineDir(self, machineName):
    return '/var/lib/vcycle/shared/spaces/' + self.spaceName + '/current/' + machineName

  def getFileContents(self, machineName, fileName):
    # Get the contents of a file for the given machine
    try:
      return open(self.machineDir(machineName) + '/' + fileName, 'r').read().strip()
    except:
      return None

  def setFileContents(self, machineName, fileName, contents, mode = stat.S_IRUSR|stat.S_IWUSR|stat.S_IRGRP):
    # Set the contents of a file for the given machine
    vcycle.vacutils.createFile(self.machineDir(machineName) + '/' + fileName, contents, mode, '/var/lib/vcycle/shared/tmp')

  def connect(self):
    # Null method in case this API doesn't need a connect step
    pass

  def _xmlToDictRecursor(self, xmlTree):

    tag      = xmlTree.tag.split('}')[1]
    retDict  = { tag: {} }
    children = list(xmlTree)

    if children:
      childrenLists = collections.defaultdict(list)

      for recursorDict in map(self._xmlToDictRecursor, children):
        for key, value in recursorDict.iteritems():
          childrenLists[key].append(value)

      childrenDict = {}
      for key, value in childrenLists.iteritems():
         # Value is always a list, so ask for value[0] even if single child
         childrenDict[key] = value

      retDict = { tag: childrenDict }

    if xmlTree.attrib:
      retDict[tag].update(('@' + key, value) for key, value in xmlTree.attrib.iteritems())

    if xmlTree.text and xmlTree.text.strip():
      retDict[tag]['#text'] = xmlTree.text.strip()

    return retDict

  def _xmlToDict(self, xmlString):
    # Convert XML string to dictionary
    # - Each tag below root has a list of dictionaries as its value even if length 1 (or 0!)
    # - Text contained within the tag itself appears as key #text
    # - Attributes of the tag appear as key @attributename
    return self._xmlToDictRecursor(xml.etree.cElementTree.XML(xmlString))

  def httpRequest(self,
                  url, 			# HTTP(S) URL to contact
                  request = None, 	# = jsonRequest for compatibility
                  jsonRequest = None, 	# dictionary to be converted to JSON body (overrides formRequest)
                  formRequest = None,   # dictionary to be converted into HTML Form body, or body itself
                  headers = None, 	# request headers
                  verbose = False, 	# turn on Curl logging messages
                  method = None, 	# DELETE, otherwise always GET/POST
                  anyStatus = False	# accept any HTTP status without exception, not just 2xx
                 ):

    # Returns dictionary:  { 'headers' : HEADERS, 'response' : DICTIONARY, 'raw' : string, 'status' : CURL RESPONSE CODE }

    self.curl.unsetopt(pycurl.CUSTOMREQUEST)
    self.curl.setopt(pycurl.URL, str(url))
    self.curl.setopt(pycurl.USERAGENT, 'Vcycle ' + vcycleVersion)

    # backwards compatible
    if request:
      jsonRequest = request

    if method and method.upper() == 'DELETE':
      self.curl.setopt(pycurl.CUSTOMREQUEST, 'DELETE')
    elif jsonRequest:
      try:
        self.curl.setopt(pycurl.POSTFIELDS, json.dumps(jsonRequest))
      except Exception as e:
        raise VcycleError('JSON encoding of "' + str(jsonRequest) + '" fails (' + str(e) + ')')
    elif formRequest:

      if isinstance(formRequest, dict):
        # if formRequest is a dictionary then encode it
        try:
          self.curl.setopt(pycurl.POSTFIELDS, urllib.urlencode(formRequest))
        except Exception as e:
          raise VcycleError('Form encoding of "' + str(formRequest) + '" fails (' + str(e) + ')')
      else:
        # otherwise assume formRequest is already formatted
        try:
          self.curl.setopt(pycurl.POSTFIELDS, formRequest)
        except Exception as e:
          raise VcycleError('Form encoding of "' + str(formRequest) + '" fails (' + str(e) + ')')

    else :
      # No body, just GET and headers
      self.curl.setopt(pycurl.HTTPGET, True)

    outputBuffer = StringIO.StringIO()
    self.curl.setopt(pycurl.WRITEFUNCTION, outputBuffer.write)

    headersBuffer = StringIO.StringIO()
    self.curl.setopt(pycurl.HEADERFUNCTION, headersBuffer.write)

    # Set up the list of headers to send in the request
    allHeaders = []

    if jsonRequest:
      allHeaders.append('Content-Type: application/json')
      allHeaders.append('Accept: application/json')
    elif formRequest:
      allHeaders.append('Content-Type: application/x-www-form-urlencoded')

    if headers:
      allHeaders.extend(headers)

    self.curl.setopt(pycurl.HTTPHEADER, allHeaders)

    if verbose:
      self.curl.setopt(pycurl.VERBOSE, 2)
    else:
      self.curl.setopt(pycurl.VERBOSE, 0)

    self.curl.setopt(pycurl.TIMEOUT,        curlTimeOutSeconds)
    self.curl.setopt(pycurl.FOLLOWLOCATION, False)
    self.curl.setopt(pycurl.SSL_VERIFYPEER, 1)
    self.curl.setopt(pycurl.SSL_VERIFYHOST, 2)
    self.curl.setopt(pycurl.SSLVERSION,     pycurl.SSLVERSION_TLSv1)

    if hasattr(self, 'usercert') and hasattr(self, 'userkey') and self.usercert and self.userkey:
      if self.usercert[0] == '/':
        self.curl.setopt(pycurl.SSLCERT, self.usercert)
      else :
        self.curl.setopt(pycurl.SSLCERT, '/var/lib/vcycle/spaces/' + self.spaceName + '/' + self.usercert)

      if self.userkey[0] == '/':
        self.curl.setopt(pycurl.SSLKEY, self.userkey)
      else :
        self.curl.setopt(pycurl.SSLKEY, '/var/lib/vcycle/spaces/' + self.spaceName + '/' + self.userkey)

    if os.path.isdir('/etc/grid-security/certificates'):
      self.curl.setopt(pycurl.CAPATH, '/etc/grid-security/certificates')

    try:
      self.curl.perform()
    except Exception as e:
      raise VcycleError('Failed to read ' + url + ' (' + str(e) + ')')

    headersBuffer.seek(0)
    outputHeaders = { }

    while True:

      try:
        oneLine = headersBuffer.readline().strip()
      except:
        break

      if not oneLine:
        break

      if oneLine.startswith('HTTP/1.1 '):
        # An HTTP return code, overwrite any previous code
        outputHeaders['status'] = [ oneLine[9:] ]

        if oneLine == 'HTTP/1.1 100 Continue':
          # Silently eat the blank line
          oneLine = headersBuffer.readline().strip()

      else:
        # Otherwise a Name: Value header
        headerNameValue = oneLine.split(':',1)

        # outputHeaders is a dictionary of lowercased header names
        # but the values are always lists, with one or more values (if multiple headers with the same name)
        if headerNameValue[0].lower() not in outputHeaders:
          outputHeaders[ headerNameValue[0].lower() ] = []

        outputHeaders[ headerNameValue[0].lower() ].append( headerNameValue[1].strip() )

    if 'content-type' in outputHeaders and outputHeaders['content-type'][0].startswith('application/json'):
      try:
        response = json.loads(outputBuffer.getvalue())
      except:
        response = None

    elif 'content-type' in outputHeaders and outputHeaders['content-type'][0] == 'text/xml':
      try:
        response = self._xmlToDict(outputBuffer.getvalue())
      except:
        response = None

    else:
      response = None

    # If not a 2xx code then raise an exception unless anyStatus option given
    if not anyStatus and self.curl.getinfo(pycurl.RESPONSE_CODE) / 100 != 2:
      try:
        vcycle.vacutils.logLine('Query raw response: ' + str(outputBuffer.getvalue()))
      except:
        pass

      raise VcycleError('Query of ' + url + ' returns HTTP code ' + str(self.curl.getinfo(pycurl.RESPONSE_CODE)))

    return { 'headers' : outputHeaders, 'response' : response, 'raw' : str(outputBuffer.getvalue()), 'status' : self.curl.getinfo(pycurl.RESPONSE_CODE) }

  def _deleteOneMachine(self, machineName, shutdownMessage = None):

    vcycle.vacutils.logLine('Deleting ' + machineName + ' in ' + self.spaceName + ':' +
                            str(self.machines[machineName].machinetypeName) + ', in state ' + str(self.machines[machineName].state))

    # record when this was tried (not when done, since don't want to overload service with failing deletes)
    self.setFileContents(machineName, 'deleted', str(int(time.time())))

    if shutdownMessage and not os.path.exists('/var/lib/vcycle/machines/' + machineName + '/joboutputs/shutdown_message'):
      try:
        self.setFileContents(machineName, 'joboutputs/shutdown_message', shutdownMessage)
      except:
        pass

    # Call the subclass method specific to this space
    self.deleteOneMachine(machineName)

  def deleteMachines(self):
    # Delete machines in this space. We do not update totals here: next cycle is good enough.

    for machineName,machine in self.machines.iteritems():

      if not machine.managedHere:
        # We do not delete machines that are not managed by this Vcycle instance
        continue

      if machine.deletedTime and (machine.deletedTime > int(time.time()) - 3600):
        # We never try deletions more than once every 60 minutes
        continue

      # Delete machines as appropriate
      if machine.state == MachineState.starting and \
         (machine.createdTime is None or
          (self.maxStartingSeconds and
           machine.createdTime < int(time.time()) - self.maxStartingSeconds)):
        # We try to delete failed-to-start machines after maxStartingSeconds (default 3600)
        self._deleteOneMachine(machineName, '700 Failed to start')

      elif machine.state == MachineState.failed or \
           machine.state == MachineState.shutdown or \
           machine.state == MachineState.deleting:
        # Delete non-starting, non-running machines
        self._deleteOneMachine(machineName)

      elif machine.state == MachineState.running and \
           machine.machinetypeName in self.machinetypes and \
           machine.startedTime and \
           (int(time.time()) > (machine.startedTime + self.machinetypes[machine.machinetypeName].max_wallclock_seconds)):
        vcycle.vacutils.logLine(machineName + ' exceeded max_wallclock_seconds')
        self._deleteOneMachine(machineName, '700 Exceeded max_wallclock_seconds')

      elif machine.state == MachineState.running and \
           machine.machinetypeName in self.machinetypes and \
           self.machinetypes[machine.machinetypeName].heartbeat_file and \
           self.machinetypes[machine.machinetypeName].heartbeat_seconds and \
           machine.startedTime and \
           (int(time.time()) > (machine.startedTime + self.machinetypes[machine.machinetypeName].fizzle_seconds)) and \
           (int(time.time()) > (machine.startedTime + self.machinetypes[machine.machinetypeName].heartbeat_seconds)) and \
           (
            (machine.heartbeatTime is None) or
            (machine.heartbeatTime < (int(time.time()) - self.machinetypes[machine.machinetypeName].heartbeat_seconds))
           ):
        vcycle.vacutils.logLine(machineName + 
                                ' failed to update heartbeat file (heartbeatTime = ' + 
                                str(machine.heartbeatTime) + 
                                ', < ' + 
                                str(int(time.time()) - self.machinetypes[machine.machinetypeName].heartbeat_seconds) + 
                                ')')
        self._deleteOneMachine(machineName, '700 Heartbeat file not updated')

      # Check shutdown times
      elif machine.state == MachineState.running and \
           machine.machinetypeName in self.machinetypes:
        shutdowntime = self.updateShutdownTime(machine)
        if shutdowntime is not None and int(time.time()) > shutdowntime:
          # log what has passed
          if self.shutdownTime == shutdowntime:
            vcycle.vacutils.logLine(
                'shutdown time ({}) for space {} has passed'
                .format(shutdowntime, self.spaceName))
          else:
            vcycle.vacutils.logLine(
                'shutdown time ({}) for machine {} has passed'
                .format(shutdowntime, machineName))
          self._deleteOneMachine(machineName, '700 Passed shutdowntime')

  def moveMachineDirectories(self):
    """ Go through /var/lib/vcycle/shared/spaces/SPACENAME/current/, moving directory trees
        for now absent machines to deleted directory ie deletion by the cloud has now happened """

    try:
      dirslist = os.listdir('/var/lib/vcycle/shared/spaces/' + self.spaceName + '/current')
    except:
      return
 
    # Make sure the directory we move finished machines directories to is there
    try:
      os.makedirs('/var/lib/vcycle/shared/spaces/' + self.spaceName + '/deleted',
                  stat.S_IWUSR + stat.S_IXUSR + stat.S_IRUSR + stat.S_IXGRP + stat.S_IRGRP + stat.S_IXOTH + stat.S_IROTH)
    except:
      pass

    # Go through the per-machine directories
    for machineName in dirslist:

      if self.machines is not None and machineName in self.machines:
        # We never delete/log directories for machines that are still listed
        continue

      # Move the directory structure to the stopped machines directory
      vcycle.vacutils.logLine('Save ' + machineName + ' files to deleted directory')
      os.rename(self.machineDir(machineName), '/var/lib/vcycle/shared/spaces/' + self.spaceName + '/deleted/' + machineName)

  def cleanupDeletedDirectories(self):
    """ Go through /var/lib/vcycle/shared/SPACE/deleted deleting expired directory trees """

    try:
      dirslist = os.listdir('/var/lib/vcycle/shared/spaces/' + self.spaceName + '/deleted/')
    except:
      return
      
    expireTime = int(time.time() - self.cleanup_hours * 3600)

    # Go through the per-machine directories
    for machineName in dirslist:
    
      if int(os.stat('/var/lib/vcycle/shared/spaces/' + self.spaceName + '/deleted/' + machineName).st_mtime) < expireTime:
        vcycle.vacutils.logLine('Cleanup directory of ' + machineName + ' in ' + self.spaceName)
        
        try:
          shutil.rmtree('/var/lib/vcycle/shared/spaces/' + self.spaceName + '/deleted/' + machineName)
          vcycle.vacutils.logLine('Deleted /var/lib/vcycle/shared/spaces/' + self.spaceName + '/deleted/' + machineName)
        except:
          vcycle.vacutils.logLine('Failed deleting /var/lib/vcycle/shared/spaces/' + self.spaceName + '/deleted/' + machineName)

  def takeMachines(self):
    # Take abandoned machines from other managers (Vcycle instances), based on their manager_heartbeat times
    # We do this at the end of the cycle to prevent race conditions mattering
    # (things settle down during the end of cycle sleep)

    for machineName,machine in self.machines.iteritems():

      if machine.managedHere:
        # We do not process machines that are already managed by this Vcycle instance
        continue

      # We add a random tolerance of up to 100% to takeSeconds in case there is more than
      # one valid Vcycle instance. Each will take an equal share of abandoned machines during
      # that extra tolerance period.
      if machine.managerHeartbeatTime < time.time() - takeSeconds * (1.0 + random.random()):
        vcycle.vacutils.logLine('Will take ' + machineName + ' in ' + self.spaceName + ' from manager ' + str(machine.manager))
        try:
          # First try to change the manager name
          machine.setFileContents('manager', os.uname()[1])          
        except Exception as e:
          # If that fails, bail out. Hopefully another manager will successfully take it? Or we will next cycle?
          vcycle.vacutils.logLine('Failed changing manager for ' + machineName + ' in ' + self.spaceName)
        else:
          # If it succeeds, then update the heartbeat immediately to stop another manager taking it 
          machine.setFileContents('manager_heartbeat', str(int(time.time())))
          vcycle.vacutils.logLine('Have taken ' + machineName + ' in ' + self.spaceName + ' from manager ' + str(machine.manager))
          
  def createHeartbeatMachines(self):
    # Create a list of machines in each machinetype, to be populated
    # with machine names of machines with a current heartbeat
    # Permissions o+x to allow httpd to read specific lists but not
    # allow directory browsing
    try:
      os.makedirs('/var/lib/vcycle/shared/spaces/' + self.spaceName + '/heartbeatlists',
                stat.S_IWUSR + stat.S_IXUSR + stat.S_IRUSR + stat.S_IXGRP + stat.S_IRGRP + stat.S_IXOTH)
    except:
      pass

    for machinetypeName in self.machinetypes:
       self.machinetypes[machinetypeName].heartbeatMachines = []

    for machineName,machine in self.machines.iteritems():
      if machine.managedHere and \
         machine.state == MachineState.running and \
         machine.machinetypeName in self.machinetypes and \
         self.machinetypes[machine.machinetypeName].heartbeat_file and \
         self.machinetypes[machine.machinetypeName].heartbeat_seconds and \
         machine.startedTime and \
         (
          (machine.heartbeatTime is not None) and
          (machine.heartbeatTime > (int(time.time()) - self.machinetypes[machine.machinetypeName].heartbeat_seconds))
         ):
        # An active machine producing its heartbeat
        self.machinetypes[machine.machinetypeName].heartbeatMachines.append(machineName)

    # Save these lists as files accessible through the web server    
    for machinetypeName in self.machinetypes:
      fileContents = []
      for machineName in self.machinetypes[machinetypeName].heartbeatMachines:
        fileContents.append('%d %s %s\n' 
                        % (self.machines[machineName].heartbeatTime, machineName, self.machines[machineName].ip))

      # Sort the list by heartbeat time, newest first, then write as a file
      fileContents.sort(reverse=True)
      vcycle.vacutils.createFile('/var/lib/vcycle/shared/spaces/' + self.spaceName + '/heartbeatlists/' + machinetypeName, ''.join(fileContents), 0664, '/var/lib/vcycle/shared/tmp')
      
  def makeFactoryMessage(self, cookie = '0'):
    factoryHeartbeatTime = int(time.time())

    try:
      mjfHeartbeatTime = int(os.stat('/var/log/httpd/https-vcycle.log').st_ctime)
      metadataHeartbeatTime = mjfHeartbeatTime
    except:
      mjfHeartbeatTime = 0
      metadataHeartbeatTime = 0

    try:
      bootTime = int(time.time() - float(open('/proc/uptime','r').readline().split()[0]))
    except:
      bootTime = 0

    daemonDiskStatFS  = os.statvfs('/var/lib/vcycle')
    rootDiskStatFS = os.statvfs('/tmp')

    memory = vcycle.vacutils.memInfo()

    try:
      osIssue = open('/etc/issue.vac','r').readline().strip()
    except:
      try:
        osIssue = open('/etc/issue','r').readline().strip()
      except:
        osIssue = os.uname()[2]

    if spaces[self.spaceName].gocdb_sitename:
      tmpGocdbSitename = spaces[self.spaceName].gocdb_sitename
    else:
      tmpGocdbSitename = '.'.join(self.spaceName.split('.')[1:]) if '.' in self.spaceName else self.spaceName

    messageDict = {
                'message_type'             : 'factory_status',
                'daemon_version'           : 'Vcycle ' + vcycleVersion + ' vcycled',
                'vacquery_version'         : 'VacQuery ' + vacQueryVersion,
                'cookie'                   : cookie,
                'space'                    : self.spaceName,
                'site'                     : tmpGocdbSitename,
                'factory'                  : os.uname()[1],
                'time_sent'                : int(time.time()),

                'running_processors'       : self.runningProcessors,
                'running_machines'         : self.runningMachines,

                'max_processors'           : self.processors_limit,
                'max_machines'             : self.processors_limit,

                'root_disk_avail_kb'       : (rootDiskStatFS.f_bavail * rootDiskStatFS.f_frsize) / 1024,
                'root_disk_avail_inodes'   : rootDiskStatFS.f_favail,

                'daemon_disk_avail_kb'      : (daemonDiskStatFS.f_bavail *  daemonDiskStatFS.f_frsize) / 1024,
                'daemon_disk_avail_inodes'  : daemonDiskStatFS.f_favail,

                'load_average'             : vcycle.vacutils.loadAvg(2),
                'kernel_version'           : os.uname()[2],
                'os_issue'                 : osIssue,
                'boot_time'                : bootTime,
                'factory_heartbeat_time'   : factoryHeartbeatTime,
                'mjf_heartbeat_time'       : mjfHeartbeatTime,
                'metadata_heartbeat_time'  : metadataHeartbeatTime,
                'swap_used_kb'             : memory['SwapTotal'] - memory['SwapFree'],
                'swap_free_kb'             : memory['SwapFree'],
                'mem_used_kb'              : memory['MemTotal'] - memory['MemFree'],
                'mem_total_kb'             : memory['MemTotal']
                  }

    if self.runningHS06 is not None:
      messageDict['max_hs06']     = self.runningHS06
      messageDict['running_hs06'] = self.runningHS06

    return json.dumps(messageDict)

  def makeMachinetypeMessages(self, cookie = '0'):
    messages = []
    timeNow = int(time.time())
    numMachinetypes = len(spaces[self.spaceName].machinetypes)

    if spaces[self.spaceName].gocdb_sitename:
      tmpGocdbSitename = spaces[self.spaceName].gocdb_sitename
    else:
      tmpGocdbSitename = '.'.join(self.spaceName.split('.')[1:]) if '.' in self.spaceName else self.spaceName

    for machinetypeName in spaces[self.spaceName].machinetypes:
      messageDict = {
                'message_type'          : 'machinetype_status',
                'daemon_version'        : 'Vcycle ' + vcycleVersion + ' vcycled',
                'vacquery_version'      : 'VacQuery ' + vacQueryVersion,
                'cookie'                : cookie,
                'space'                 : self.spaceName,
                'site'                  : tmpGocdbSitename,
                'factory'               : os.uname()[1],
                'num_machinetypes'      : numMachinetypes,
                'time_sent'             : timeNow,

                'machinetype'           : machinetypeName,
                'bytes_per_processor'   : spaces[self.spaceName].machinetypes[machinetypeName].rss_bytes_per_processor,
                'running_machines'      : spaces[self.spaceName].machinetypes[machinetypeName].runningMachines,
                'running_processors'    : spaces[self.spaceName].machinetypes[machinetypeName].runningProcessors
                     }

      try:
        messageDict['fqan'] = spaces[self.spaceName].machinetypes[machinetypeName].accounting_fqan
      except:
        pass

      if spaces[self.spaceName].machinetypes[machinetypeName].runningHS06 is not None:
        messageDict['running_hs06'] = spaces[self.spaceName].machinetypes[machinetypeName].runningHS06
        
      if spaces[self.spaceName].machinetypes[machinetypeName].max_wallclock_seconds is not None:
        messageDict['max_wallclock_seconds'] = spaces[self.spaceName].machinetypes[machinetypeName].max_wallclock_seconds

      if spaces[self.spaceName].machinetypes[machinetypeName].max_processors is not None:
        messageDict['max_processors'] = spaces[self.spaceName].machinetypes[machinetypeName].max_processors
        
      messages.append(json.dumps(messageDict))

    return messages

  def updateShutdownTime(self, machine):
    """ If there is a space shutdown time update machines to this value if it
        is closer than their value.
        Return closest shutdown time.
    """
    try:
      shutdowntime_job = int(machine.getFileContents(
        'jobfeatures/shutdowntime_job'))
    except:
      shutdowntime_job = None

    # use space shutdownTime if shutdowntime_job is None
    # or shutdowntime_job has passed
    if self.shutdownTime is not None and \
       (shutdowntime_job is None or \
        shutdowntime_job > self.shutdownTime):
      machine.setFileContents('jobfeatures/shutdowntime_job', str(self.shutdownTime))
      shutdowntime_job = self.shutdownTime

    return shutdowntime_job

  def sendVacMon(self):

    if not self.vacmons:
      return

    factoryMessage      = self.makeFactoryMessage()
    machinetypeMessages = self.makeMachinetypeMessages()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    for vacmonHostPort in self.vacmons:
      (vacmonHost, vacmonPort) = vacmonHostPort.split(':')

      vcycle.vacutils.logLine('Sending VacMon status messages to %s:%s' % (vacmonHost, vacmonPort))

      sock.sendto(factoryMessage, (vacmonHost,int(vacmonPort)))

      for machinetypeMessage in machinetypeMessages:
        sock.sendto(machinetypeMessage, (vacmonHost,int(vacmonPort)))

    sock.close()

  def makeMachines(self):

    if self.shutdownTime is not None and self.shutdownTime < time.time():
      vcycle.vacutils.logLine('Space {} has shutdown time in the past ({}), '\
          'not allocating any more machines'.format(
            self.spaceName, self.shutdownTime))
      return

    vcycle.vacutils.logLine('Space ' + self.spaceName +
                            ' has ' + str(self.runningProcessors) +
                            ' processor(s) found allocated to running Vcycle VMs out of ' + str(self.totalProcessors) +
                            ' found in any state for any machinetype or none.')

    if self.processors_limit is None:
      vcycle.vacutils.logLine('The limit for the number of processors which may be allocated is not known to Vcycle.')
    else:
      vcycle.vacutils.logLine('Vcycle knows the limit on the number of processors is %d, either from its configuration or from the infrastructure.' % self.processors_limit)


    for machinetypeName,machinetype in self.machinetypes.iteritems():
      vcycle.vacutils.logLine('machinetype ' + machinetypeName +
                              ' has ' + str(machinetype.startingProcessors) +
                              ' starting and ' + str(machinetype.runningProcessors) +
                              ' running processors out of ' + str(machinetype.totalProcessors) +
                              ' found in any state. ' + str(machinetype.notPassedFizzle) +
                              ' not passed fizzle_seconds(' + str(machinetype.fizzle_seconds) +
                              '). ')

    creationsPerCycle  = int(0.9999999 + self.processors_limit * 0.1)
    creationsThisCycle = 0

    # Keep making passes through the machinetypes until limits exhausted
    while True:
      if self.processors_limit is not None and self.totalProcessors >= self.processors_limit:
        vcycle.vacutils.logLine('Reached limit (%d) on number of processors to allocate for space %s' % (self.processors_limit, self.spaceName))
        return

      if creationsThisCycle >= creationsPerCycle:
        vcycle.vacutils.logLine('Already reached limit of %d processor allocations this cycle' % creationsThisCycle )
        return

      # For each pass, machinetypes are visited in a random order
      machinetypeNames = self.machinetypes.keys()
      random.shuffle(machinetypeNames)

      # Will record the best machinetype to create
      bestMachinetypeName = None

      for machinetypeName in machinetypeNames:
        if self.machinetypes[machinetypeName].target_share <= 0.0:
          continue

        if self.machinetypes[machinetypeName].processors_limit is not None and self.machinetypes[machinetypeName].totalProcessors >= self.machinetypes[machinetypeName].processors_limit:
          vcycle.vacutils.logLine('Reached limit (' + str(self.machinetypes[machinetypeName].processors_limit) + ') on number of processors to allocate for machinetype ' + machinetypeName)
          continue

        if self.machinetypes[machinetypeName].max_starting_processors is not None and self.machinetypes[machinetypeName].startingProcessors >= self.machinetypes[machinetypeName].max_starting_processors:
          vcycle.vacutils.logLine('Reached limit (%d) on processors that can be in starting state for machinetype %s' % (self.machinetypes[machinetypeName].max_starting_processors, machinetypeName))
          continue

        if int(time.time()) < (self.machinetypes[machinetypeName].lastAbortTime + self.machinetypes[machinetypeName].backoff_seconds):
          vcycle.vacutils.logLine('Free capacity found for %s ... but only %d seconds after last abort'
                                  % (machinetypeName, int(time.time()) - self.machinetypes[machinetypeName].lastAbortTime) )
          continue

        if (int(time.time()) < (self.machinetypes[machinetypeName].lastAbortTime +
                                self.machinetypes[machinetypeName].backoff_seconds +
                                self.machinetypes[machinetypeName].fizzle_seconds)) and \
           (self.machinetypes[machinetypeName].notPassedFizzle > 0):
          vcycle.vacutils.logLine('Free capacity found for ' +
                                  machinetypeName +
                                  ' ... but still within fizzle_seconds+backoff_seconds(' +
                                  str(int(self.machinetypes[machinetypeName].backoff_seconds + self.machinetypes[machinetypeName].fizzle_seconds)) +
                                  ') of last abort (' +
                                  str(int(time.time()) - self.machinetypes[machinetypeName].lastAbortTime) +
                                  's ago) and ' +
                                  str(self.machinetypes[machinetypeName].notPassedFizzle) +
                                  ' starting/running but not yet passed fizzle_seconds (' +
                                  str(self.machinetypes[machinetypeName].fizzle_seconds) + ')')
          continue

        if (not bestMachinetypeName) or (self.machinetypes[machinetypeName].weightedMachines < self.machinetypes[bestMachinetypeName].weightedMachines):
          bestMachinetypeName = machinetypeName

      if bestMachinetypeName:
        vcycle.vacutils.logLine('Free capacity found for ' + bestMachinetypeName + ' within ' + self.spaceName + ' ... creating')

        # This tracks creation attempts, whether successful or not
        creationsThisCycle += self.machinetypes[bestMachinetypeName].min_processors
        self.machinetypes[bestMachinetypeName].startingProcessors += self.machinetypes[bestMachinetypeName].min_processors
        self.machinetypes[bestMachinetypeName].notPassedFizzle += 1

        try:
          self._createMachine(bestMachinetypeName)
        except Exception as e:
          vcycle.vacutils.logLine('Failed creating machine with machinetype ' + bestMachinetypeName + ' in ' + self.spaceName + ' (' + str(e) + ')')

      else:
        vcycle.vacutils.logLine('No more free capacity and/or suitable machinetype found within ' + self.spaceName)
        return

  def _createMachine(self, machinetypeName):
    """Generic machine creation"""

    try:
      machineName = self.machinetypes[machinetypeName].makeMachineName()
    except Exception as e:
      vcycle.vacutils.logLine('Failed constructing new machine name (' + str(e) + ')')

    try:
      shutil.rmtree(self.machineDir(machineName))
      vcycle.vacutils.logLine('Found and deleted left over ' + self.machineDir(machineName))
    except:
      pass

    os.makedirs(self.machineDir(machineName) + '/machinefeatures',
                stat.S_IWUSR + stat.S_IXUSR + stat.S_IRUSR + stat.S_IXGRP + stat.S_IRGRP + stat.S_IXOTH + stat.S_IROTH)
    os.makedirs(self.machineDir(machineName) + '/jobfeatures',
                stat.S_IWUSR + stat.S_IXUSR + stat.S_IRUSR + stat.S_IXGRP + stat.S_IRGRP + stat.S_IXOTH + stat.S_IROTH)
    os.makedirs(self.machineDir(machineName) + '/joboutputs',
                stat.S_IWUSR + stat.S_IXUSR + stat.S_IRUSR + stat.S_IWGRP + stat.S_IXGRP + stat.S_IRGRP +
                stat.S_IWOTH + stat.S_IXOTH + stat.S_IROTH)

    self.setFileContents(machineName, 'created',          str(int(time.time())))
    self.setFileContents(machineName, 'updated',          str(int(time.time())))
    self.setFileContents(machineName, 'machinetype_name', machinetypeName)
    self.setFileContents(machineName, 'space_name',       self.spaceName)
    self.setFileContents(machineName, 'manager',          os.uname()[1])
    
    if self.machinetypes[machinetypeName].https_x509dn:
      self.setFileContents(machineName, 'https_x509dn', self.machinetypes[machinetypeName].https_x509dn, mode=0644)
    
    if self.zones:
      zone = random.choice(self.zones)
      self.setFileContents(machineName, 'zone', zone)
    else:
      zone = None

    if self.machinetypes[machinetypeName].root_image and (self.machinetypes[machinetypeName].root_image.startswith('http://') or self.machinetypes[machinetypeName].root_image.startswith('https://')):
      rootImageURL = self.machinetypes[machinetypeName].root_image
    else:
      rootImageURL = None    
      
    userDataOptions = self.machinetypes[machinetypeName].options.copy()
    
    if self.machinetypes[machinetypeName].cvmfsProxyMachinetype:
      # If we define a cvmfs_proxy_machinetype, then use the IPs of heartbeat producing
      # machines of that machinetype to create the user_data_option_cvmfs_proxy
      # Any existing value for that option is appended to the list, using the semicolon syntax
      
      if self.machinetypes[machinetypeName].cvmfsProxyMachinetype not in self.machinetypes:
        raise VcycleError('Machinetype %s (cvmfs_proxy_machinetype) does not exist!'
                               % self.machinetypes[machinetypeName].cvmfsProxyMachinetype)
                               
      ipList = []
      for heartbeatMachineName in self.machinetypes[self.machinetypes[machinetypeName].cvmfsProxyMachinetype].heartbeatMachines:
        ipList.append('http://%s:%d' % (self.machines[heartbeatMachineName].ip, self.machinetypes[machinetypeName].cvmfsProxyMachinetypePort))

      if ipList:
        # We only change any existing value if we found machines of cvmfs_proxy_machinetype
        if 'user_data_option_cvmfs_proxy' not in userDataOptions:
          existingProxyOption = ''
        else:
          existingProxyOption = ';' + userDataOptions['user_data_option_cvmfs_proxy']

        userDataOptions['user_data_option_cvmfs_proxy'] = '|'.join(ipList) + existingProxyOption
      else:
        vcycle.vacutils.logLine('No machines found in machinetype %s (cvmfs_proxy_machinetype) - using defaults'
                                 % self.machinetypes[machinetypeName].cvmfsProxyMachinetype)
        
    try:
      userDataContents = vcycle.vacutils.createUserData(shutdownTime         = int(time.time() +
                                                                                   self.machinetypes[machinetypeName].max_wallclock_seconds),
                                                        machinetypePath      = self.machinetypes[machinetypeName].machinetype_path,
                                                        options              = userDataOptions,
                                                        versionString        = 'Vcycle ' + vcycleVersion,
                                                        spaceName            = self.spaceName,
                                                        machinetypeName      = machinetypeName,
                                                        userDataPath         = self.machinetypes[machinetypeName].user_data,
                                                        rootImageURL         = rootImageURL,
                                                        hostName             = machineName,
                                                        uuidStr              = None,
                                                        machinefeaturesURL   = 'https://' + self.https_host + ':' + str(self.https_port) + '/machines/' + self.spaceName + '/' + machineName + '/machinefeatures',
                                                        jobfeaturesURL       = 'https://' + self.https_host + ':' + str(self.https_port) + '/machines/' + self.spaceName + '/' + machineName + '/jobfeatures',
                                                        joboutputsURL        = 'https://' + self.https_host + ':' + str(self.https_port) + '/machines/' + self.spaceName + '/' + machineName + '/joboutputs',
                                                        heartbeatMachinesURL = 'https://' + self.https_host + ':' + str(self.https_port) + '/heartbeatlists/' + self.spaceName,
                                                        gocdbSitename        =  spaces[self.spaceName].gocdb_sitename
                                                       )
    except Exception as e:
      raise VcycleError('Failed getting user_data file (' + str(e) + ')')

    try:
      self.setFileContents(machineName, 'user_data', userDataContents)
    except:
      raise VcycleError('Failed to writing ' + machineName + '/user_data')

    # Create MJF shutdowntime values as these are used in deleting failed machines

    # check for existence of shutdownTime and whether wallclock limit is closer anyway
    if (self.shutdownTime is None or
        int(time.time()) + self.machinetypes[machinetypeName].maxWallclockSeconds < self.shutdownTime):
      self.setFileContents(machineName,'machinefeatures/shutdowntime',
                                str(int(time.time()) + self.machinetypes[machinetypeName].max_wallclock_seconds), mode = 0644)

    else:
      self.setFileContents(machineName,'machinefeatures/shutdowntime', str(self.shutdownTime), mode = 0644)
      self.setFileContents(machineName,'jobfeatures/shutdowntime_job', str(self.shutdownTime), mode = 0644)

    # Call the API-specific method to actually create the machine
    try:
      self.createMachine(machineName, machinetypeName, zone)
    except Exception as e:
      vcycle.vacutils.logLine('Creation of machine %s fails with: %s' % (machineName, str(e)))

    # Rest of MJF. Some values may be set by self.createMachine() from the API!

    # $MACHINEFEATURES first

    # We maintain the fiction that this is a single-VM hypervisor, as we don't know the hypervisor details
    self.setFileContents(machineName, 'machinefeatures/jobslots', "1", mode = 0644)
    self.setFileContents(machineName, 'machinefeatures/total_cpu', str(self.machines[machineName].processors), mode = 0644)

    # phys_cores and log_cores keys are deprecated
    self.setFileContents(machineName, 'machinefeatures/phys_cores', str(self.machines[machineName].processors), mode = 0644)
    self.setFileContents(machineName, 'machinefeatures/log_cores', str(self.machines[machineName].processors), mode = 0644)

    if self.machinetypes[machinetypeName].hs06_per_processor:
      self.setFileContents(machineName, 'machinefeatures/hs06', 
                           str(self.machinetypes[machinetypeName].hs06_per_processor * self.machines[machineName].processors), mode = 0644)

    # Then $JOBFEATURES

    self.setFileContents(machineName, 'jobfeatures/wall_limit_secs', 
                         str(self.machinetypes[machinetypeName].max_wallclock_seconds), mode = 0644)

    # We assume worst case that CPU usage is limited by wallclock limit
    self.setFileContents(machineName, 'jobfeatures/cpu_limit_secs', 
                         str(self.machinetypes[machinetypeName].max_wallclock_seconds), mode = 0644)

    # Calculate MB for this VM ("job")
    self.setFileContents(machineName, 'jobfeatures/max_rss_bytes', 
                         str(self.machinetypes[machinetypeName].rss_bytes_per_processor * self.machines[machineName].processors), 
                         mode = 0644)

    # All the cpus are allocated to this one VM ("job")
    self.setFileContents(machineName, 'jobfeatures/allocated_cpu', str(self.machines[machineName].processors), mode = 0644)

    # allocated_CPU key name is deprecated
    self.setFileContents(machineName, 'jobfeatures/allocated_CPU', str(self.machines[machineName].processors), mode = 0644)

    self.setFileContents(machineName, 'jobfeatures/jobstart_secs', str(int(time.time())), mode = 0644)

    if self.machines[machineName].uuidStr is not None:
      self.setFileContents(machineName, 'jobfeatures/jobstart_secs', self.machines[machineName].uuidStr, mode = 0644)

    if self.machinetypes[machinetypeName].hs06_per_processor:
      self.setFileContents(machineName, 'jobfeatures/hs06_job', 
                           str(self.machinetypes[machinetypeName].hs06_per_processor * self.machines[machineName].processors), mode = 0644)

    # We do not know max_swap_bytes, scratch_limit_bytes etc so ignore them

  def oneCycle(self):

    try:
      self.connect()
    except Exception as e:
      vcycle.vacutils.logLine('Skipping ' + self.spaceName + ' this cycle: ' + str(e))
      return

    try:
      self.scanMachines()
    except Exception as e:
      vcycle.vacutils.logLine('Giving up on ' + self.spaceName + ' this cycle: ' + str(e))
      return

    try:
      self.sendVacMon()
    except Exception as e:
      vcycle.vacutils.logLine('Sending VacMon messages fails: ' + str(e))

    try:
      self.deleteMachines()
    except Exception as e:
      vcycle.vacutils.logLine('Deleting old machines in ' + self.spaceName + ' fails: ' + str(e))
      # We carry on because this isn't fatal
      
    try:
      self.moveMachineDirectories()
    except Exception as e:
      vcycle.vacutils.logLine('Moving delete machine directoriess in ' + self.spaceName + ' fails: ' + str(e))
      # We carry on because this isn't fatal
      
    try:
       self.createHeartbeatMachines()
    except Exception as e:
      vcycle.vacutils.logLine('Creating heartbeat machine lists for ' + self.spaceName + ' fails: ' + str(e))
      
    try:
      self.makeMachines()
    except Exception as e:
      vcycle.vacutils.logLine('Making machines in ' + self.spaceName + ' fails: ' + str(e))

    try:
      self.cleanupDeletedDirectories()
    except Exception as e:
      vcycle.vacutils.logLine('Cleanup of deleted directories in ' + self.spaceName + ' fails: ' + str(e))
      # We carry on because this isn't fatal
      
    # This must be done last in the cycle to avoid race conditions between manager instances
    try:
      self.takeMachines()
    except Exception as e:
      vcycle.vacutils.logLine('Take abandoned machines ' + self.spaceName + ' fails: ' + str(e))
      
def readConf(printConf = False, updatePipes = True):

  global vcycleVersion, spaces

  try:
    f = open('/var/lib/vcycle/VERSION', 'r')
    vcycleVersion = f.readline().split('=',1)[1].strip()
    f.close()
  except:
    vcycleVersion = '0.0.0'

  spaces = {}

  parser = ConfigParser.RawConfigParser()

  # Look for configuration files in /etc/vcycle.d
  
  for etcPath in ['/var/lib/vcycle/shared/vcycle.d/', '/etc/vcycle.d/']:
    try:
      confFiles = os.listdir(etcPath)
    except:
      pass
    else:
      for oneFile in sorted(confFiles):
        if oneFile[-5:] == '.conf':
          try:
            parser.read(etcPath + oneFile)
          except Exception as e:
            vcycle.vacutils.logLine('Failed to parse ' + etcPath + oneFile + ' (' + str(e) + ')')

  # Standalone configuration file, read last in case of manual overrides
  parser.read('/etc/vcycle.conf')

  # Find the space sections
  for spaceSectionName in parser.sections():

    try:
      (sectionType, spaceName) = spaceSectionName.lower().split(None,1)
    except Exception as e:
      raise VcycleError('Cannot parse section name [' + spaceSectionName + '] (' + str(e) + ')')

    if sectionType == 'space':

      if string.translate(spaceName, None, '0123456789abcdefghijklmnopqrstuvwxyz-.') != '':
        raise VcycleError('Name of space section [space ' + spaceName + '] can only contain a-z 0-9 - or .')

      try:
        api = parser.get(spaceSectionName, 'api')
      except:
        raise VcycleError('api missing from [space ' + spaceName + ']')

      if string.translate(api, None, '0123456789abcdefghijklmnopqrstuvwxyz_') != '':
        raise VcycleError('Name of api in [space ' + spaceName + '] can only contain a-z 0-9 or _')

      try:
        apiVersion = parser.get(spaceSectionName, 'api_version')
      except:
        apiVersion = None
      else:
        if string.translate(apiVersion, None, '0123456789abcdefghijklmnopqrstuvwxyz._-') != '':
          raise VcycleError('Name of api_version in [space ' + spaceName + '] can only contain a-z 0-9 - . or _')

      for subClass in BaseSpace.__subclasses__():
        if subClass.__name__ == api.capitalize() + 'Space':
          try:
            spaces[spaceName] = subClass(api, apiVersion, spaceName, parser, spaceSectionName, updatePipes)
          except Exception as e:
            raise VcycleError('Failed to initialise space ' + spaceName + ' (' + str(e) + ')')
          else:
            break

      if spaceName not in spaces:
        raise VcycleError(api + ' is not a supported API for managing spaces')

      if parser.has_option(spaceSectionName, 'gocdb_sitename'):
        spaces[spaceName].gocdb_sitename = parser.get(spaceSectionName,'gocdb_sitename')
      else:
        spaces[spaceName].gocdb_sitename = None

      if parser.has_option(spaceSectionName, 'vacmon_hostport'):
        try:
          spaces[spaceName].vacmons = parser.get(spaceSectionName,'vacmon_hostport').lower().split()
        except:
          raise VcycleError('Failed to parse vacmon_hostport for space ' + spaceName)

        for v in spaces[spaceName].vacmons:
          if re.search('^[a-z0-9.-]+:[0-9]+$', v) is None:
            raise VcycleError('Failed to parse vacmon_hostport: must be host.domain:port')
      else:
        spaces[spaceName].vacmons = []

      if parser.has_option(spaceSectionName, 'https_host'):
        spaces[spaceName].https_host = parser.get(spaceSectionName,'https_host').strip().lower()

        if string.translate(api, None, '0123456789abcdefghijklmnopqrstuvwxyz-.') != '':
          raise VcycleError('https_host in [space ' + spaceName + '] can only contain a-z 0-9 - or .')
      else:
        spaces[spaceName].https_host = os.uname()[1]

      try:
        spaces[spaceName].https_port = int(parser.get(spaceSectionName,'https_port').strip())
      except:
        spaces[spaceName].https_port = 443

      try:
        spaces[spaceName].cleanup_hours = int(parser.get(spaceSectionName,'cleanup_hours').strip())
      except:
        spaces[spaceName].cleanup_hours = 72

    elif sectionType != 'machinetype' and sectionType != 'vacuum_pipe':
      raise VcycleError('Section type ' + sectionType + 'not recognised')

  # else: Skip over vacuum_pipe and machinetype sections, which are parsed during the space class initialization

  if printConf:
    print 'Configuration including any machinetypes from Vacuum Pipes:'
    print
    parser.write(sys.stdout)
    print

### END ###
