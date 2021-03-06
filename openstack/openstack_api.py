#!/usr/bin/python
#
#  openstack_api.py - an OpenStack plugin for Vcycle
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
import time
import json
import shutil
import string
import pycurl
import random
import base64
import StringIO
import tempfile
import calendar

import vcycle.vacutils
import vcycle.openstack.image_api

class OpenstackError(Exception):
  pass

class OpenstackSpace(vcycle.BaseSpace):

  def __init__(self, api, apiVersion, spaceName, parser, spaceSectionName, updatePipes):
  # Initialize data structures from configuration files

    # Generic initialization
    vcycle.BaseSpace.__init__(self, api, apiVersion, spaceName, parser, spaceSectionName, updatePipes)

    # OpenStack-specific initialization
    try:
      self.project_name = parser.get(spaceSectionName, 'tenancy_name')
    except:
      try:
        self.project_name = parser.get(spaceSectionName, 'project_name')
      except Exception as e:
        raise OpenstackError('project_name is required in OpenStack [space ' + spaceName + '] (' + str(e) + ')')
    else:
      vcycle.vacutils.logLine('tenancy_name in [space ' + self.spaceName + '] is deprecated - please use project_name')

    try:
      self.domain_name = parser.get(spaceSectionName, 'domain_name')
    except Exception as e:
      self.domain_name = 'default'

    try:
      self.network_uuid = parser.get(spaceSectionName, 'network_uuid')
    except Exception as e:
      self.network_uuid = None

    try:
      self.region = parser.get(spaceSectionName, 'region')
    except Exception as e:
      self.region = None

    try:
      self.zones = parser.get(spaceSectionName, 'zones').split()
    except Exception as e:
      self.zones = None
      
    try:
      self.security_groups = parser.get(spaceSectionName, 'security_groups').split()
    except Exception as e:
      self.security_groups = None    

    try:
      self.identityURL = parser.get(spaceSectionName, 'url')
    except Exception as e:
      raise OpenstackError('url is required in OpenStack [space ' + spaceName + '] (' + str(e) + ')')

    try:
      self.glanceAPIVersion = parser.get(spaceSectionName, 'glance_api')
    except Exception as e:
      raise OpenstackError('glance_api is required in OpenStack [space '
          + spaceName + '] (' + str(e) + ')')

    # For username/password authentication

    try:
      self.username = parser.get(spaceSectionName, 'username')
    except Exception as e:
      self.username = None

    try:
      # We use Base64 encoding so browsing around casually
      # doesn't reveal passwords in a memorable way.
      self.password = base64.b64decode(parser.get(spaceSectionName, 'password_base64').strip()).strip()
    except Exception:
      self.password = ''
      
    # For application credential authentication

    try:
      self.cred_id = parser.get(spaceSectionName, 'cred_id')
    except Exception as e:
      self.cred_id = None

    try:
      self.cred_secret = parser.get(spaceSectionName, 'cred_secret')
    except Exception as e:
      self.cred_secret = None
      
#    # For X.509 certificate and key file names
#
#    try:
#      self.usercert = parser.get(spaceSectionName, 'usercert')
#    except Exception as e:
#      self.usercert = None
#
#    try:
#      self.userkey = parser.get(spaceSectionName, 'userkey')
#    except Exception as e:
#      self.userkey = None
#
#    # If only one file given, then assume it contains both cert and key
#    if self.usercert and not self.userkey:
#      self.userkey = self.usercert
#    elif self.userkey and not self.usercert:
#      self.usercert = self.userkey
#
#    if (not self.username or not self.password) and not self.usercert and (not self.cred_id or not self.cred_secret):
#      raise OpenstackError('username or cred_id/cred_secret or usercert/userkey is required in OpenStack [space ' + spaceName + '] (' + str(e) + ')')

    if (not self.username or not self.password) and (not self.cred_id or not self.cred_secret):
      raise OpenstackError('username or cred_id/cred_secret is required in OpenStack [space ' + spaceName + '] (' + str(e) + ')')

    if self.apiVersion and self.apiVersion != '2' and not self.apiVersion.startswith('2.') and self.apiVersion != '3' and not self.apiVersion.startswith('3.'):
      raise OpenstackError('api_version %s not recognised' % self.apiVersion)

  def connect(self):
  # Wrapper around the connect methods and some common post-connection updates

    if not self.apiVersion or self.apiVersion == '2' or self.apiVersion.startswith('2.'):
      self._connectV2()
    elif self.apiVersion == '3' or self.apiVersion.startswith('3.'):
      self._connectV3()
    else:
      # This rechecks the checking done in the constructor called by readConf()
      raise OpenstackError('api_version %s not recognised' % self.apiVersion)

    # Save token locally for debugging with openstack command-line client
    vcycle.vacutils.createFile('/var/lib/vcycle/spaces/' + self.spaceName + '/token',
                               self.token, tmpDir = '/var/lib/vcycle/tmp')

    # initialise glance api (has to be here as we don't have imageURL until
    # after connecting)
    if self.glanceAPIVersion == '2':
      self.imageAPI = vcycle.openstack.image_api.GlanceV2(self.token, self.imageURL)
    elif self.glanceAPIVersion == '1':
      self.imageAPI = vcycle.openstack.image_api.GlanceV1(self.token, self.imageURL)
    else:
      raise OpenstackError('glanceAPIVersion %s not recongnised'
          % self.glanceAPIVersion)


    # Build dictionary of flavor details using API
    self._getFlavors()

    # Try to get the limit on the number of processors in this project
    processorsLimit =  self._getProcessorsLimit()

    # Try to use it for this space
    if self.processors_limit is None:
      vcycle.vacutils.logLine('No limit on processors set in Vcycle configuration')
      if processorsLimit is not None:
        vcycle.vacutils.logLine('Processors limit set to %d from OpenStack' % processorsLimit)
        self.processors_limit = processorsLimit
    else:
      vcycle.vacutils.logLine('Processors limit set to %d in Vcycle configuration' % self.processors_limit)

  def _connectV2(self):
  # Connect to the OpenStack service with Identity v2

    try:
      result = self.httpRequest(self.identityURL + '/tokens',
                                jsonRequest = { 'auth' : { 'tenantName' : self.project_name,
                                                           'passwordCredentials' : { 'username' : self.username, 'password' : self.password }
                                                         }
                                              }
                               )
    except Exception as e:
      raise OpenstackError('Cannot connect to ' + self.identityURL + ' with v2 API (' + str(e) + ')')

    self.token = str(result['response']['access']['token']['id'])

    self.computeURL = None
    self.imageURL   = None
    self.volumeURL  = None

    for endpoint in result['response']['access']['serviceCatalog']:
      if endpoint['type'] == 'compute':
        self.computeURL = str(endpoint['endpoints'][0]['publicURL'])
      elif endpoint['type'] == 'image':
        self.imageURL = str(endpoint['endpoints'][0]['publicURL'])
      elif endpoint['type'].startswith('volume'):
        self.volumeURL = str(endpoint['endpoints'][0]['publicURL'])

    if not self.computeURL:
      raise OpenstackError('No compute service URL found from ' + self.identityURL)

    if not self.imageURL:
      raise OpenstackError('No image service URL found from ' + self.identityURL)

    vcycle.vacutils.logLine('Connected to ' + self.identityURL + ' for space ' + self.spaceName)
    vcycle.vacutils.logLine('computeURL = ' + self.computeURL)
    vcycle.vacutils.logLine('imageURL   = ' + self.imageURL)
    vcycle.vacutils.logLine('volumeURL  = ' + str(self.volumeURL))

  def _connectV3(self):
  # Connect to the OpenStack service with Identity v3

    if self.cred_id and self.cred_secret:
      jsonRequest = { "auth": { "identity": { "methods" : [ "application_credential"],
                                              "application_credential": {
                                                                          "id":     self.cred_id,
                                                                          "secret": self.cred_secret
                                                                        }
                                            }
                              }
                    }
    else:
      jsonRequest = { "auth": { "identity": { "methods" : [ "password"],
                                              "password": {
                                                            "user": {
                                                                      "name"    : self.username,
                                                                      "domain"  : { "name": self.domain_name },
                                                                      "password": self.password
                                                                    }
                                                          }
                                            },
                                "scope": { "project": { "domain"  : { "name": self.domain_name }, "name": self.project_name } }
                              }
                    }

    try:
      # No trailing slash of identityURL! (matches URL on Horizon Dashboard API page)
      result = self.httpRequest(self.identityURL + '/auth/tokens', jsonRequest = jsonRequest)
    except Exception as e:
        raise OpenstackError('Cannot connect to ' + self.identityURL + ' with v' + self.apiVersion + ' API (' + str(e) + ')')

    try:
      self.token = result['headers']['x-subject-token'][0]
    except Exception as e:
      raise OpenstackError('Cannot read X-Subject-Token: from ' + self.identityURL + ' response with v' + self.apiVersion + ' API (' + str(e) + ')')

    self.computeURL = None
    self.imageURL   = None
    self.volumeURL  = None

    # This might be a bit naive? We just keep the LAST matching one we see.
    for service in result['response']['token']['catalog']:

      if service['type'] == 'compute':
        for endpoint in service['endpoints']:
          if endpoint['interface'] == 'public' and \
              (self.region is None or self.region == endpoint['region']):
            self.computeURL = str(endpoint['url'])

      elif service['type'] == 'image':
        for endpoint in service['endpoints']:
          if endpoint['interface'] == 'public' and \
              (self.region is None or self.region == endpoint['region']):
            self.imageURL = str(endpoint['url'])

      elif service['type'].startswith('volume'):
        for endpoint in service['endpoints']:
          if endpoint['interface'] == 'public' and \
              (self.region is None or self.region == endpoint['region']):
            self.volumeURL = str(endpoint['url'])

    if not self.computeURL:
      raise OpenstackError('No compute service URL found from ' + self.identityURL)

    if not self.imageURL:
      raise OpenstackError('No image service URL found from ' + self.identityURL)

    vcycle.vacutils.logLine('Connected to ' + self.identityURL + ' for space ' + self.spaceName)
    vcycle.vacutils.logLine('computeURL = ' + self.computeURL)
    vcycle.vacutils.logLine('imageURL   = ' + self.imageURL)
    vcycle.vacutils.logLine('volumeURL  = ' + str(self.volumeURL))

  def _getFlavors(self):
    """Query OpenStack to get details of flavors defined for this project"""

    self.flavors = {}

    try:
      result = self.httpRequest(self.computeURL + '/flavors/detail',
                                headers = [ 'X-Auth-Token: ' + self.token ])
    except Exception as e:
      raise OpenstackError('Cannot connect to ' + self.computeURL + ' (' + str(e) + ')')

    for oneFlavor in result['response']['flavors']:

      flavor = {}
      flavor['mb']          = oneFlavor['ram']
      flavor['processors']  = oneFlavor['vcpus']
      flavor['id']          = oneFlavor['id']

      self.flavors[oneFlavor['name']] = flavor

  def _getProcessorsLimit(self):
    """Query OpenStack to get processor limit for this project"""

    try:
      result = self.httpRequest(self.computeURL + '/limits',
                                headers = [ 'X-Auth-Token: ' + self.token ])
    except Exception as e:
      raise OpenstackError('Cannot connect to ' + self.computeURL + ' (' + str(e) + ')')

    try:
      return int(result['response']['limits']['absolute']['maxTotalCores'])
    except:
      return None

  def scanMachines(self):
    """Query OpenStack compute service for details of machines in this space"""

    # For each machine found in the space, this method is responsible for
    # either (a) ignorning non-Vcycle VMs but updating self.totalProcessors
    # or (b) creating a Machine object for the VM in self.spaces

    try:
      result = self.httpRequest(self.computeURL + '/servers/detail',
                                headers = [ 'X-Auth-Token: ' + self.token ])
    except Exception as e:
      raise OpenstackError('Cannot connect to ' + self.computeURL + ' (' + str(e) + ')')

    # Convert machines from None to an empty dictionary since we successfully connected
    self.machines = {}

    for oneServer in result['response']['servers']:

      try:
        machineName = str(oneServer['metadata']['name'])
      except:
        machineName = oneServer['name']

      try:
        flavorID = oneServer['flavor']['id']
      except:
        flavorID   = None
        processors = 1
      else:
        try:
          processors = self.flavors[self.getFlavorName(flavorID)]['processors']
        except:
          processors = 1

      # Just in case other VMs are in this space
      if machineName[:7] != 'vcycle-':
        # Still count VMs that we didn't create and won't manage, to avoid going above space limit
        self.totalProcessors += processors
        continue

      uuidStr = str(oneServer['id'])

      # Try to get the IP address. Always use the zeroth member of the earliest network
      try:
        ip = str(oneServer['addresses'][ min(oneServer['addresses']) ][0]['addr'])
      except:
        ip = '0.0.0.0'

      createdTime  = calendar.timegm(time.strptime(str(oneServer['created']), "%Y-%m-%dT%H:%M:%SZ"))
      updatedTime  = calendar.timegm(time.strptime(str(oneServer['updated']), "%Y-%m-%dT%H:%M:%SZ"))

      try:
        startedTime = calendar.timegm(time.strptime(str(oneServer['OS-SRV-USG:launched_at']).split('.')[0], "%Y-%m-%dT%H:%M:%S"))
      except:
        startedTime = None

      taskState  = str(oneServer['OS-EXT-STS:task_state'])
      powerState = int(oneServer['OS-EXT-STS:power_state'])
      status     = str(oneServer['status'])

      try:
        machinetypeName = str(oneServer['metadata']['machinetype'])
      except:
        machinetypeName = None
      else:
        if machinetypeName not in self.machinetypes:
          machinetypeName = None

      try:
        zone = str(oneServer['OS-EXT-AZ:availability_zone'])
      except:
        zone = None

      if taskState == 'Deleting':
        state = vcycle.MachineState.deleting
      elif status == 'ACTIVE' and powerState == 1:
        state = vcycle.MachineState.running
      elif status == 'BUILD' or status == 'ACTIVE':
        state = vcycle.MachineState.starting
      elif status == 'SHUTOFF':
        state = vcycle.MachineState.shutdown
      elif status == 'ERROR':
        state = vcycle.MachineState.failed
      elif status == 'DELETED':
        state = vcycle.MachineState.deleting
      else:
        state = vcycle.MachineState.unknown

      self.machines[machineName] = vcycle.shared.Machine(name             = machineName,
                                                         spaceName        = self.spaceName,
                                                         state            = state,
                                                         ip               = ip,
                                                         createdTime      = createdTime,
                                                         startedTime      = startedTime,
                                                         updatedTime      = updatedTime,
                                                         uuidStr          = uuidStr,
                                                         machinetypeName  = machinetypeName,
                                                         zone             = zone,
                                                         processors       = processors)

  def getFlavorName(self, flavorID):
    """Get the "flavor" ID"""

    for flavorName in self.flavors:
      if self.flavors[flavorName]['id'] == flavorID:
        return flavorName

    raise OpenstackError('Flavor "' + flavorID + '" not available!')

  def getImageID(self, machinetypeName):
    """ Get the image ID """

    # If we already know the image ID, then just return it
    if hasattr(self.machinetypes[machinetypeName], '_imageID'):
      if self.machinetypes[machinetypeName]._imageID:
        return self.machinetypes[machinetypeName]._imageID
      else:
        # If _imageID is None, then it's not available for this cycle
        raise OpenstackError('Image "' + self.machinetypes[machinetypeName].root_image + '" for machinetype ' + machinetypeName + ' not available!')

    # Get the existing images for this tenancy
    result = self.imageAPI.getImageDetails()

    # Specific image, not managed by Vcycle, lookup ID
    if self.machinetypes[machinetypeName].root_image[:6] == 'image:':
      for image in result['response']['images']:
         if self.machinetypes[machinetypeName].root_image[6:] == image['name']:
           self.machinetypes[machinetypeName]._imageID = str(image['id'])
           return self.machinetypes[machinetypeName]._imageID

      raise OpenstackError('Image "' + self.machinetypes[machinetypeName].root_image[6:] + '" for machinetype ' + machinetypeName + ' not available!')

    # Always store/make the image name
    if self.machinetypes[machinetypeName].root_image[:7] == 'http://' or \
       self.machinetypes[machinetypeName].root_image[:8] == 'https://' or \
       self.machinetypes[machinetypeName].root_image[0] == '/':
      imageName = self.machinetypes[machinetypeName].root_image
    else:
      imageName = '/var/lib/vcycle/spaces/' + self.spaceName + '/machinetypes/' + machinetypeName + '/files/' + self.machinetypes[machinetypeName].root_image

    # Find the local copy of the image file
    if not hasattr(self.machinetypes[machinetypeName], '_imageFile'):

      if self.machinetypes[machinetypeName].root_image[:7] == 'http://' or \
         self.machinetypes[machinetypeName].root_image[:8] == 'https://':

        try:
          imageFile = vcycle.vacutils.getRemoteRootImage(self.machinetypes[machinetypeName].root_image,
                                         '/var/lib/vcycle/imagecache',
                                         '/var/lib/vcycle/tmp',
                                         'Vcycle ' + vcycle.shared.vcycleVersion)

          imageLastModified = int(os.stat(imageFile).st_mtime)
        except Exception as e:
          raise OpenstackError('Failed fetching ' + self.machinetypes[machinetypeName].root_image + ' (' + str(e) + ')')

        self.machinetypes[machinetypeName]._imageFile = imageFile

      elif self.machinetypes[machinetypeName].root_image[0] == '/':

        try:
          imageLastModified = int(os.stat(self.machinetypes[machinetypeName].root_image).st_mtime)
        except Exception as e:
          raise OpenstackError('Image file "' + self.machinetypes[machinetypeName].root_image + '" for machinetype ' + machinetypeName + ' does not exist!')

        self.machinetypes[machinetypeName]._imageFile = self.machinetypes[machinetypeName].root_image

      else: # root_image is not an absolute path, but imageName is

        try:
          imageLastModified = int(os.stat(imageName).st_mtime)
        except Exception as e:
          raise OpenstackError('Image file "' + self.machinetypes[machinetypeName].root_image +
                            '" does not exist in /var/lib/vcycle/spaces/' + self.spaceName + '/machinetypes/' + machinetypeName + '/files/ !')

        self.machinetypes[machinetypeName]._imageFile = imageName

    else:
      imageLastModified = int(os.stat(self.machinetypes[machinetypeName]._imageFile).st_mtime)

    # Go through the existing images looking for a name and time stamp match
    # We should delete old copies of the current image name if we find them here
    # Glance v2 api differs by keeping metadata in tags
    if self.glanceAPIVersion == '1':
      for image in result['response']['images']:
        try:
          if image['name'] == imageName and \
              image['status'] == 'ACTIVE' and \
              image['metadata']['last_modified'] == str(imageLastModified):
            self.machinetypes[machinetypeName]._imageID = str(image['id'])
            return self.machinetypes[machinetypeName]._imageID
        except:
          pass
    elif self.glanceAPIVersion == '2':
      for image in result['response']['images']:
        try:
          if image['name'] == imageName and image['status'] == 'active':
            for tag in image['tags']:
              if tag.lstrip('last_modified: ') == str(imageLastModified):
                self.machinetypes[machinetypeName]._imageID = str(image['id'])
                return self.machinetypes[machinetypeName]._imageID
        except:
          pass

    vcycle.vacutils.logLine('Image "' + self.machinetypes[machinetypeName].root_image + '" not found in image service, so uploading')

    if self.machinetypes[machinetypeName].cernvm_signing_dn:
      cernvmDict = vac.vacutils.getCernvmImageData(self.machinetypes[machinetypeName]._imageFile)

      if cernvmDict['verified'] == False:
        raise OpenstackError('Failed to verify signature/cert for ' + self.machinetypes[machinetypeName].root_image)
      elif re.search(self.machinetypes[machinetypeName].cernvm_signing_dn,  cernvmDict['dn']) is None:
        raise OpenstackError('Signing DN ' + cernvmDict['dn'] + ' does not match cernvm_signing_dn = ' + self.machinetypes[machinetypeName].cernvm_signing_dn)
      else:
        vac.vacutils.logLine('Verified image signed by ' + cernvmDict['dn'])

    # Try to upload the image
    try:
      self.machinetypes[machinetypeName]._imageID = self.uploadImage(self.machinetypes[machinetypeName]._imageFile, imageName, imageLastModified)
      return self.machinetypes[machinetypeName]._imageID
    except Exception as e:
      raise OpenstackError('Failed to upload image file ' + imageName + ' (' + str(e) + ')')

  def uploadImage(self, imageFile, imageName, imageLastModified,
                  verbose = False):
    return self.imageAPI.uploadImage(imageFile, imageName, imageLastModified,
                                     verbose)

  def getKeyPairName(self, machinetypeName):
    """Get the key pair name from root_public_key"""

    if hasattr(self.machinetypes[machinetypeName], '_keyPairName'):
      if self.machinetypes[machinetypeName]._keyPairName:
        return self.machinetypes[machinetypeName]._keyPairName
      else:
        raise OpenstackError('Key pair "' + self.machinetypes[machinetypeName].root_public_key + '" for machinetype ' + machinetypeName + ' not available!')

    # Get the ssh public key from the root_public_key file

    if self.machinetypes[machinetypeName].root_public_key[0] == '/':
      try:
        f = open(self.machinetypes[machinetypeName].root_public_key, 'r')
      except Exception as e:
        OpenstackError('Cannot open ' + self.machinetypes[machinetypeName].root_public_key)
    else:
      try:
        f = open('/var/lib/vcycle/spaces/' + self.spaceName + '/machinetypes/' + self.machinetypeName + '/files/' + self.machinetypes[machinetypeName].root_public_key, 'r')
      except Exception as e:
        OpenstackError('Cannot open /var/lib/vcycle/spaces/' + self.spaceName + '/machinetypes/' + self.machinetypeName + '/files/' + self.machinetypes[machinetypeName].root_public_key)

    while True:
      try:
        line = f.read()
      except:
        raise OpenstackError('Cannot find ssh-rsa public key line in ' + self.machinetypes[machinetypeName].root_public_key)

      if line[:8] == 'ssh-rsa ':
        sshPublicKey =  line.split(' ')[1]
        break

    # Check if public key is there already

    try:
      result = self.httpRequest(self.computeURL + '/os-keypairs',
                                headers = [ 'X-Auth-Token: ' + self.token ])
    except Exception as e:
      raise OpenstackError('Cannot connect to ' + self.computeURL + ' (' + str(e) + ')')

    for keypair in result['response']['keypairs']:
      try:
        if 'ssh-rsa ' + sshPublicKey + ' vcycle' == keypair['keypair']['public_key']:
          self.machinetypes[machinetypeName]._keyPairName = str(keypair['keypair']['name'])
          return self.machinetypes[machinetypeName]._keyPairName
      except:
        pass

    # Not there so we try to add it

    keyName = str(time.time()).replace('.','-')

    try:
      result = self.httpRequest(self.computeURL + '/os-keypairs',
                                jsonRequest = { 'keypair' : { 'name'       : keyName,
                                                              'public_key' : 'ssh-rsa ' + sshPublicKey + ' vcycle'
                                                            }
                                              },
                                headers = [ 'X-Auth-Token: ' + self.token ])
    except Exception as e:
      raise OpenstackError('Cannot connect to ' + self.computeURL + ' (' + str(e) + ')')

    vcycle.vacutils.logLine('Created key pair ' + keyName + ' for ' + self.machinetypes[machinetypeName].root_public_key + ' in ' + self.spaceName)

    self.machinetypes[machinetypeName]._keyPairName = keyName
    return self.machinetypes[machinetypeName]._keyPairName

  def deleteVolumes(self):
  
    try:
      listResult = self.httpRequest(self.volumeURL + '/volumes',
                                  headers = [ 'X-Auth-Token: ' + self.token ])
    except Exception as e:
      raise OpenstackError('Cannot connect to ' + self.volumeURL + ' (' + str(e) + ')')
  
    for volume in listResult['response']['volumes']:
    
      uuidStr = str(volume['id'])
   
      print 'Try to delete ' + str(volume['name'])
    
      try:
        deleteResult = self.httpRequest(self.volumeURL + '/volumes/' + uuidStr,
                                        method = 'DELETE',
                                        headers = [ 'X-Auth-Token: ' + self.token ])
      except Exception as e:
        print str(e)

  def createVolume(self, machineName, machinetypeName, processors, zone):
    # Create a volume synchronously
    # Volume is created with the same name as its intended machine

    request = { 
                "volume" : {
                             "size"    : self.volume_gb_per_processor * processors,
                             "imageRef": self.getImageID(machinetypeName),    
                             "name"    : machineName
                           } 
              }
                                   
    if zone:
      request['volume']['availability_zone'] = zone
    
    try:
      result = self.httpRequest(self.volumeURL + '/volumes',
                                jsonRequest = request,
                                headers = [ 'X-Auth-Token: ' + self.token ])
    except Exception as e:
      raise OpenstackError('Cannot connect to ' + self.volumeURL + ' (' + str(e) + ')')

    try:
      uuidStr = str(result['response']['volume']['id'])
    except:
      raise OpenstackError('Could not get volume UUID from volume creation response (' + str(e) + ')')

    vcycle.vacutils.logLine('Created volume ' + machineName + ' (' + uuidStr + ') within ' + self.spaceName)

    startTime = int(time.time())

    # Wait for volume to become available. Not ideal since may take a while
    # 120 is a hardcoded timeout of 120 seconds
    while int(time.time()) < startTime + 120:
    
      try:
        infoResult = self.httpRequest(self.volumeURL + '/volumes/' + uuidStr,
                                      headers = [ 'X-Auth-Token: ' + self.token ])
      except Exception as e:
        raise OpenstackError('Cannot connect to ' + self.volumeURL + ' (' + str(e) + ')')
  
      if infoResult['response']['volume']['status'] == 'available':
        vcycle.vacutils.logLine('Volume ' + machineName + ' (' + uuidStr + ') is now available')
        return uuidStr
        
      time.sleep(10) # Hardcoded checking interval of 10 seconds

    raise OpenstackError('Volume ' + machineName + ' (' + uuidStr + ') failed to become available - timeout reached')

#    request = { "os-attach": {
#                               "host_name" : machineName,
#                               "mountpoint": "/dev/vdd" # Hardcoded but ignored anyway?
#                             }
#              }
#
#    return
#
#              
#    try:
#      result = self.httpRequest(self.volumeURL + '/volumes/' + uuidStr + '/action',
#                                jsonRequest = request,
#                                headers = [ 'X-Auth-Token: ' + self.token ])
#    except Exception as e:
#      raise OpenstackError('Cannot connect to ' + self.volumeURL + ' (' + str(e) + ')')
#
#    if result['status'] != 202:
#      raise OpenstackError('Attaching volume %s to %s fails with code %d' % (uuidStr, machineName, result['status']))
#      
#    vcycle.vacutils.logLine('Attached volume ' + machineName + ' (' + uuidStr + ')  within ' + self.spaceName)
    
  def createMachine(self, machineName, machinetypeName, zone = None):
    # OpenStack-specific machine creation steps
    
    # Find the first flavor matching min_processors:max_processors
    flavorName = None
    
    for fn in self.machinetypes[machinetypeName].flavor_names:
      if fn in self.flavors:
        if self.machinetypes[machinetypeName].min_processors <= self.flavors[fn]['processors'] and \
           (self.machinetypes[machinetypeName].max_processors is None or \
            self.machinetypes[machinetypeName].max_processors >= self.flavors[fn]['processors']):
          flavorName = fn
          break
    
    if not flavorName:
      raise OpenstackError('No flavor suitable for machinetype ' + machinetypeName)

    if self.volume_gb_per_processor:
      uuidVolume = self.createVolume(machineName, machinetypeName, self.flavors[flavorName]['processors'], zone)
    else:
      uuidVolume = None

    try:
      request = { 'server' :
                  { 'user_data' : base64.b64encode(self.getFileContents(machineName, 'user_data')),
                    'name'      : machineName,
                    'imageRef'  : self.getImageID(machinetypeName),
                    'flavorRef' : self.flavors[flavorName]['id'],
                    'metadata'  : { 'cern-services'   : 'false',
                                    'name'	      : machineName,
                                    'machinetype'     : machinetypeName,
                                    'machinefeatures' : 'https://' + self.https_host + ':' + str(self.https_port) + '/machines/' + self.spaceName + '/' + machineName + '/machinefeatures',
                                    'jobfeatures'     : 'https://' + self.https_host + ':' + str(self.https_port) + '/machines/' + self.spaceName + '/' + machineName + '/jobfeatures',
                                    'joboutputs'      : 'https://' + self.https_host + ':' + str(self.https_port) + '/machines/' + self.spaceName + '/' + machineName + '/joboutputs'  }
                  }
                }

      if self.network_uuid:
        request['server']['networks'] = [{"uuid": self.network_uuid}]
        vcycle.vacutils.logLine('Will use network %s for %s' % (self.network_uuid, machineName))

      if zone:
        request['server']['availability_zone'] = zone
        vcycle.vacutils.logLine('Will request %s be created in zone %s of space %s' % (machineName, zone, self.spaceName))

      if self.security_groups:
        request['server']['security_groups'] = []
        for security_group in self.security_groups:
          request['server']['security_groups'].append( { "name" : security_group } )

        vcycle.vacutils.logLine('Will request %s be created in security groups %s of space %s' % (machineName, str(self.security_groups), self.spaceName))

      if self.machinetypes[machinetypeName].root_public_key:
        request['server']['key_name'] = self.getKeyPairName(machinetypeName)

      if uuidVolume:
        time.sleep(60) # UNTIL WE HAVE HANDLE THE STATE PROPERLY
        request['server']['block_device_mapping_v2'] = [{ "source_type" : "volume",
                                                          "uuid"        : uuidVolume,  
                                                          "delete_on_termination" : True,
                                                          "boot_index": 0,
                                                          "destination_type" : "volume"
                                                       }]

#      if self.volume_gb_per_processor:
#        request['server']['block_device_mapping_v2'] = [{ "source_type" : "blank",
#                                                          "volume_size" : self.volume_gb_per_processor * self.flavors[flavorName]['processors'],
#                                                          "delete_on_termination" : True,
#                                                          "no_device": True,
#                                                          "boot_index": -1,
#                                                          "destination_type" : "volume"
#                                                       }]

    except Exception as e:
      raise OpenstackError('Failed to create new machine %s: %s' % (machineName, str(e)))

    try:
      result = self.httpRequest(self.computeURL + '/servers',
                                jsonRequest = request,
                                headers = [ 'X-Auth-Token: ' + self.token ])
    except Exception as e:
      raise OpenstackError('Cannot connect to ' + self.computeURL + ' (' + str(e) + ')')

    try:
      uuidStr = str(result['response']['server']['id'])
    except:
      raise OpenstackError('Could not get VM UUID from VM creation response (' + str(e) + ')')

    vcycle.vacutils.logLine('Created ' + machineName + ' (' + uuidStr + ') for ' + machinetypeName + ' within ' + self.spaceName)

    self.machines[machineName] = vcycle.shared.Machine(name             = machineName,
                                                       spaceName        = self.spaceName,
                                                       state            = vcycle.MachineState.starting,
                                                       ip               = '0.0.0.0',
                                                       createdTime      = int(time.time()),
                                                       startedTime      = None,
                                                       updatedTime      = int(time.time()),
                                                       uuidStr          = uuidStr,
                                                       machinetypeName  = machinetypeName,
                                                       processors       = self.flavors[flavorName]['processors'])

  def deleteOneMachine(self, machineName):

    try:
      self.httpRequest(self.computeURL + '/servers/' + self.machines[machineName].uuidStr,
                       method = 'DELETE',
                       headers = [ 'X-Auth-Token: ' + self.token ])
    except Exception as e:
      raise vcycle.shared.VcycleError('Cannot delete ' + machineName + ' via ' + self.computeURL + ' (' + str(e) + ')')
