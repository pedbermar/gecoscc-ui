#
# Copyright 2017, Junta de Andalucia
# http://www.juntadeandalucia.es/
#
# Authors:
#   Jose Manuel Rodriguez Caro <jmrodriguez@solutia-it.es>
#
# All rights reserved - EUPL License V 1.1
# https://joinup.ec.europa.eu/software/page/eupl/licence-eupl
#

import sys

from chef import Node as ChefNode

from optparse import make_option

from gecoscc.management import BaseCommand
from gecoscc.utils import _get_chef_api, toChefUsername, get_filter_nodes_belonging_ou, apply_policies_to_computer, delete_dotted
from bson.objectid import ObjectId
from jsonschema import validate
from jsonschema.exceptions import ValidationError

import logging
logger = logging.getLogger()    
    
class Command(BaseCommand):
    description = """
       Check Local Admin Users policy for all the workstations in the database. 
       This script must be executed on major policies updates when the changes in the policies structure may
       cause problems if the Chef nodes aren't properly updated.
       
       So, the right way to use the script is:
       1) Import the new policies with the knife command
       2) Run the "import_policies" command.
       3) Run this "check_local_admin_policy" command.
    """

    usage = "usage: %prog config_uri check_local_admin_policy --administrator user --key file.pem"

    option_list = [
        make_option(
            '-a', '--administrator',
            dest='chef_username',
            action='store',
            help='An existing chef super administrator username (like "pivotal" user)'
        ),
        make_option(
            '-k', '--key',
            dest='chef_pem',
            action='store',
            help='The pem file that contains the chef administrator private key'
        ),
    ]

    required_options = (
        'chef_username',
        'chef_pem',
    )
    
    def command(self):
        # Initialization
        sanitized = False
        computers = set()
        self.api = _get_chef_api(self.settings.get('chef.url'),
                            toChefUsername(self.options.chef_username),
                            self.options.chef_pem, False, self.settings.get('chef.version'))

        self.auth_user = self.db.adminusers.find_one({'username': self.options.chef_username})
        if self.auth_user is None:
            logger.error('The administrator user must exist in MongoDB')
            sys.exit(1)

        self.db = self.pyramid.db
        
        # Get local_admin_users_res (Local Administrators) policy
        logger.info('Getting Local Administrators (local_admin_users_res) policy ...')
        policy   = self.db.policies.find_one({'slug':'local_admin_users_res'})
        schema   = policy['schema']
        policyId = policy['_id']
        
        logger.info('schema   = %s'%str(schema))
        logger.info('Id.policy = %s'%str(policyId))

        # Searching nodes with the Local Administrators policy
        # Query Fields of an Embedded Document (Mongo documentation)
        # Example:
        # db.nodes.find({"policies.58c8122a0dfd425b0894d5b6":{$exists:true}})
        logger.info('Searching nodes with the Local Administrators policy...')
        field = 'policies.' + str(policyId)
        filters  = {field:{'$exists':True}}
        nodes = self.db.nodes.find(filters)
  
        # Validating data and, where appropiate, fixing
        for node in nodes:
            instance = node['policies'][unicode(policyId)]

            logger.info('Node name = %s, _id = %s'%(node['name'],str(node['_id'])))
            logger.info('Instance before validate method: %s'%str(instance))
            while True:
                try:
                    validate(instance, schema)
                    break
                except ValidationError as e: 
                    logger.warning('Validation error on instance = %s'%str(e.message))
                    # Sanitize instance
                    self.sanitize(e, instance)
                    sanitized = True

            if sanitized:
                # Setting false sanitized for next iteration
                sanitized = False
                logger.info('Sanitized instance: %s'%str(instance))

                # Update mongo
                self.db.nodes.update({'_id': node['_id']},{'$set':{field:instance}})

                # Affected nodes
                if node['type'] == 'ou':
                    result = list(self.db.nodes.find({'path': get_filter_nodes_belonging_ou(node['_id']),'type': 'computer'},{'_id':1}))
                    logger.info('OU computers = %s'%str(result))
                elif node['type'] == 'group':
                    result = list(self.db.nodes.find({'_id':{'$in':node['members']},'type':'computer'},{'_id':1}))
                    logger.info('GROUP computers = %s'%str(result))
                elif node['type'] == 'computer':
                    result = [node]
                    logger.info('COMPUTER computers = %s'%str(result))

                [computers.add(str(n['_id'])) for n in result]


        # Removing unused local_admin_remove_list attribute in chef nodes
        for node_id in ChefNode.list():
            node = ChefNode(node_id, self.api)
            logger.info('Checking node: %s'%(node_id))
            attr_dotted = policy['path'] + '.local_admin_remove_list'
            logger.info('Atttribute dotted path: %s'%(attr_dotted))
            if node.attributes.has_dotted(attr_dotted):
                logger.info("Remove 'local_admin_remove_list' attribute!")
                try:
                    logger.info("node.attributes = %s" % str(node.attributes['gecos_ws_mgmt']['misc_mgmt']['local_admin_users_res'].to_dict()))
                    delete_dotted(node.attributes, attr_dotted)
                    node.save()
                except:
                    logger.warn("Problem deleting local_admin_remove_list value from node: %s"%(node_id))
                    logger.warn("You may be trying to delete a default attribute instead normal attribute: %s"%(node_id))

        for computer in computers:
            logger.info('computer = %s'%str(computer))
            computer = self.db.nodes.find_one({'_id': ObjectId(computer)})
            apply_policies_to_computer(self.db.nodes, computer, self.auth_user, api=self.api, initialize=False, use_celery=False)
                   
        logger.info('Finished.')


    def sanitize(self, error, instance):
        logger.info('Sanitizing ...')
        logger.info('error = %s' % str(error))
        logger.info('error.message = %s' % str(error.message))
        logger.info('instance = %s' % str(instance))
        
        # CASE 1: jsonschema.exceptions.ValidationError: u'luis' is not of type u'object'
        # Failed validating u'type' in schema[u'properties'][u'local_admin_list'][u'items']:
        #     {u'additionalProperties': False,
        #      u'mergeActionField': u'action',
        #      u'mergeIdField': [u'name'],
        #      u'order': [u'name', u'action'],
        #      u'properties': {u'action': {u'enum': [u'add', u'remove'],
        #                                  u'title': u'Action',
        #                                  u'title_es': u'Acci\xf3n',
        #                                  u'type': u'string'},
        #                      u'name': {u'title': u'Name',
        #                                u'title_es': u'Nombre',
        #                                u'type': u'string'}},
        #      u'required': [u'name', u'action'],
        #      u'type': u'object'}


        # CASE 2: jsonschema.exceptions.ValidationError: Additional properties are not allowed (u'local_admin_remove_list' was unexpected)
        # Failed validating u'additionalProperties' in schema:
        #    {u'additionalProperties': False,
        #     u'order': [u'local_admin_list'],
        #     u'properties': {u'local_admin_list': {u'description': u'Enter a local user to grant administrator rights',
        #                                           u'description_es': u'Escriba un usuario local para concederle permisos de administrador',
        #                                           u'items': {u'mergeActionField': u'action',
        #                                                      u'mergeIdField': [u'name'],
        #                                                      u'order': [u'name',
        #                                                                 u'action'],
        #                                                      u'properties': {u'action': {u'enum': [u'add',
        #                                                                                            u'remove'],
        #                                                                                  u'title': u'Action',
        #                                                                                  u'title_es': u'Acci\xf3n',
        #                                                                                  u'type': u'string'},
        #                                                                      u'name': {u'title': u'Name',
        #                                                                                u'title_es': u'Nombre',
        #                                                                                u'type': u'string'}},
        #                                                      u'required': [u'name',
        #                                                                    u'action'],
        #                                                      u'type': u'object'},
        #                                           u'title': u'users',
        #                                           u'title_es': u'Usuarios',
        #                                           u'type': u'array'}},
        #     u'required': [u'local_admin_list'],
        #     u'title': u'Local Administrators',
        #     u'title_es': u'Administradores locales',
        #     u'type': u'object'}
        #
        #On instance:
        #    {u'local_admin_list': ['ariadna'], u'local_admin_remove_list': []}


        
        # error.path: A collections.deque containing the path to the offending element within the instance. 
        #             The deque can be empty if the error happened at the root of the instance.
        # (http://python-jsonschema.readthedocs.io/en/latest/errors/#jsonschema.exceptions.ValidationError.relative_path)
        # Examples:
        # error.path = deque([u'users_list', 1, u'actiontorun'])
        # error.path = deque([u'users_list', 1])

        
        if "Additional properties are not allowed (u'local_admin_remove_list' was unexpected)" in error.message:
            for removed in instance['local_admin_remove_list']:
                instance['local_admin_list'].append({'name':removed,'action':'remove'})
            del instance['local_admin_remove_list']

        elif "u'local_admin_list' is a required property" in error.message:
            instance['local_admin_list'] = []

        elif "is not of type u'object'" in error.message:
            error.path.rotate(-1)
            index = error.path.popleft()
            logger.info('index = %s' % str(index))
            user = instance['local_admin_list'][index]
            logger.info('user = %s' % str(user))
            logger.info('BEFORE instance = %s' % str(instance))
            instance['local_admin_list'][index] = {'name': user, 'action': 'add'}
            logger.info('AFTER instance = %s' % str(instance))
        else:
            raise error
