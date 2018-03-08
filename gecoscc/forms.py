#
# Copyright 2013, Junta de Andalucia
# http://www.juntadeandalucia.es/
#
# Author:
#   Pablo Martin <goinnn@gmail.com>
#
# All rights reserved - EUPL License V 1.1
# https://joinup.ec.europa.eu/software/page/eupl/licence-eupl
#

import os, errno, shutil
import time
import re
import glob
import colander
import deform
import logging
import urllib2
import zipfile
import tempfile

from bson import ObjectId

from pkg_resources import resource_filename

from pyramid.threadlocal import get_current_registry

from chef.exceptions import ChefServerError
from deform.template import ZPTRendererFactory

from gecoscc import messages
from gecoscc.tasks import cookbook_upload, script_runner
from gecoscc.i18n import gettext as _
from gecoscc.utils import get_chef_api, create_chef_admin_user
from gecoscc.socks import maintenance_mode


default_dir = resource_filename('deform', 'templates/')
gecoscc_dir = resource_filename('gecoscc', 'templates/deform/')
gecos_renderer = ZPTRendererFactory((gecoscc_dir, default_dir))

logger = logging.getLogger(__name__)

class GecosButton(deform.Button):

    def __init__(self, name='_submit', title=None, type='submit', value=None,
                 disabled=False, css_class=None, attrs=None):
        super(GecosButton, self).__init__(name=name, title=title, type=type, value=value,
                                          disabled=False, css_class=css_class)
        self.attrs = attrs or {}


class GecosForm(deform.Form):

    template = 'form'
    item_template = 'mapping_item'
    css_class = 'deform'
    default_renderer = gecos_renderer
    sorted_fields = None

    def __init__(self, schema, action='', method='POST', buttons=(),
                 formid='deform', use_ajax=False, ajax_options='{}',
                 autocomplete=None, **kw):
        if not buttons:
            buttons = (GecosButton(title=_('Submit'),
                                   css_class='pull-right'),)
        if self.sorted_fields:
            schema.children.sort(key=lambda item: self.sorted_fields.index(item.name))
        super(GecosForm, self).__init__(schema, action=action,
                                        method=method,
                                        buttons=buttons,
                                        formid='deform',
                                        use_ajax=use_ajax,
                                        ajax_options=ajax_options,
                                        autocomplete=None, **kw)
        self.widget.template = self.template
        self.widget.item_template = self.item_template
        self.widget.css_class = self.css_class

    def created_msg(self, msg, msg_type='success'):
        messages.created_msg(self.request, msg, msg_type)


class GecosTwoColumnsForm(GecosForm):

    template = 'form_two_columns'
    item_template = 'mapping_item_two_columns'
    css_class = 'deform form-horizontal'


class BaseAdminUserForm(GecosTwoColumnsForm):

    sorted_fields = ('username', 'email', 'password',
                     'repeat_password', 'first_name', 'last_name',
                     'nav_tree_pagesize', 'policies_pagesize', 'jobs_pagesize', 'group_nodes_pagesize',
                     'ou_managed', 'ou_availables',)

    def __init__(self, schema, collection, username, request, *args, **kwargs):
        self.collection = collection
        self.username = username
        self.request = request
        super(BaseAdminUserForm, self).__init__(schema, *args, **kwargs)
        schema.children[self.sorted_fields.index('username')].ignore_unique = self.ignore_unique
        schema.children[self.sorted_fields.index('email')].ignore_unique = self.ignore_unique
        schema.children[self.sorted_fields.index('nav_tree_pagesize')].ignore_unique = self.ignore_unique
        schema.children[self.sorted_fields.index('policies_pagesize')].ignore_unique = self.ignore_unique
        schema.children[self.sorted_fields.index('jobs_pagesize')].ignore_unique = self.ignore_unique
        schema.children[self.sorted_fields.index('group_nodes_pagesize')].ignore_unique = self.ignore_unique


class AdminUserAddForm(BaseAdminUserForm):

    ignore_unique = False

    def save(self, admin_user):
        self.collection.insert(admin_user)
        admin_user['plain_password'] = self.cstruct['password']
        settings = get_current_registry().settings
        user = self.request.user
        
        api = get_chef_api(settings, user)
        try:
            create_chef_admin_user(api, settings, admin_user['username'], None, admin_user['email'])
            self.created_msg(_('User created successfully'))
            return True
        except ChefServerError as e:
            self.created_msg(e.message, 'danger')
            self.collection.remove({'username': admin_user['username']})
            raise e


class AdminUserEditForm(BaseAdminUserForm):

    ignore_unique = True

    def __init__(self, schema, collection, *args, **kwargs):
        buttons = (GecosButton(title=_('Submit'),
                               css_class='pull-right',
                               name='_submit'),
                   GecosButton(title=_('Delete'),
                               name='_delete',
                               css_class='pull-right',
                               type='button'))
        super(AdminUserEditForm, self).__init__(schema, collection, buttons=buttons, *args, **kwargs)
        schema.children[self.sorted_fields.index('password')] = schema.children[self.sorted_fields.index('password')].clone()
        schema.children[self.sorted_fields.index('repeat_password')] = schema.children[self.sorted_fields.index('repeat_password')].clone()
        schema.children[self.sorted_fields.index('password')].missing = ''
        schema.children[self.sorted_fields.index('repeat_password')].missing = ''
        self.children[self.sorted_fields.index('username')].widget.readonly = True

    def save(self, admin_user):
        if admin_user['password'] == '':
            del admin_user['password']
        self.collection.update({'username': self.username},
                               {'$set': admin_user})
        if admin_user['username'] != self.username and self.request.session['auth.userid'] == self.username:
            self.request.session['auth.userid'] = admin_user['username']
        self.created_msg(_('User edited successfully'))


class AdminUserOUManageForm(GecosTwoColumnsForm):

    ou_managed_count = colander.SchemaNode(colander.Integer(),
                                           title='',
                                           name='ou_managed_count',
                                           widget=deform.widget.HiddenWidget(),
                                           default=1)
    ou_availables_count = colander.SchemaNode(colander.Integer(),
                                              title='',
                                              name='ou_availables_count',
                                              widget=deform.widget.HiddenWidget(),
                                              default=1)

    def __init__(self, schema, collection, username, request, *args, **kwargs):
        schema.get('ou_managed').title += '<p><a href="#ou-managed" class="add-another">%s</a></p>' % _('Add another')
        schema.get('ou_availables').title += '<p><a href="#ou-availables" class="add-another">%s</a></p>' % _('Add another')
        schema.children.append(self.ou_managed_count)
        schema.children.append(self.ou_availables_count)
        super(AdminUserOUManageForm, self).__init__(schema, collection=collection,
                                                    username=username, request=request,
                                                    *args, **kwargs)

    def save(self, ous_managed):
        self.collection.update({'username': self.username},
                               {'$set': ous_managed})
        self.created_msg(_('User edited successfully'))


class AdminUserVariablesForm(GecosForm):

    def validate(self, data):
        data_dict = dict(data)
        if data_dict['auth_type'] == 'LDAP':
            for field in self.schema.get('auth_ad').children:
                field.missing = ''
            for field in self.schema.get('auth_ad_spec').children:
                field.validator = None
                field.missing = ''
        else:
            for field in self.schema.get('auth_ldap').children:
                field.missing = ''
            if data_dict.get('specific_conf', False):
                for field in self.schema.get('auth_ad').children:
                    field.validator = None
                    field.missing = ''
            else:
                for field in self.schema.get('auth_ad_spec').children:
                    field.validator = None
                    field.missing = ''
        return super(AdminUserVariablesForm, self).validate(data)

    def save(self, variables):
        if variables['auth_type'] != 'LDAP' and variables.get('specific_conf', False):
            for i, fileout in enumerate(self.schema.get_config_files('w', self.username)):
                fileout_name = fileout.name.split(os.sep)[-1]
                file_field = variables['auth_ad_spec'][fileout_name.replace('.', '_')]
                if not file_field:
                    continue
                filein = file_field['fp']
                fileout.write(filein.read())
                filein.close()
                fileout.close()
        del variables['auth_ad_spec']
        self.collection.update({'username': self.username}, {'$set': {'variables': variables}})
        self.created_msg(_('Variables updated successfully'))

class ParseMetadataException(Exception):
    pass

class CookbookUploadForm(GecosForm):

    def validate(self, data):
        data_dict = dict(data)
        if not data_dict['upload'] and data_dict['remote_file']:
            self.schema.get('local_file').missing=''

        return super(CookbookUploadForm, self).validate(data)

    def save(self, upload):
        
        logger.info("CookbookUpload - upload - %s" % upload)
        error = False
        settings = get_current_registry().settings
        rootdir = settings['cookbook_upload_rootdir'] + '/'
        logger.debug("forms.py ::: CookbookUpload - rootdir = %s" % rootdir)
        uploadir = rootdir + self.username + "/uploads/" + str(int(time.time())) + "/"
        logger.debug("forms.py ::: CookbookUpload - uploadir = %s" % uploadir)

        try:
            if not os.path.exists(uploadir):
                os.makedirs(uploadir)
            if upload['local_file']:
                f = upload['local_file']
                with open('/tmp/' + f['filename'], 'wb') as zipped: 
                    zipped.write(f['fp'].read())

            elif upload['remote_file']:
                f = urllib2.urlopen(upload['remote_file'])
                with open('/tmp/' + os.path.basename(upload['remote_file']), "wb") as zipped:
                    zipped.write(f.read())

            logger.info("forms.py ::: CookbookUpload - zipped = %s" % zipped)

            # Decompress zipfile into temporal dir
            tmpdir = tempfile.mkdtemp()
            zip_ref = zipfile.ZipFile(zipped.name,'r')
            zip_ref.extractall(tmpdir)
            zip_ref.close()

            # Getting cookbook's name
            cookbook_name = cookbook_ver = ""
            for name in glob.glob(tmpdir + "/*"):
                logger.info("forms.py ::: CookbookUpload - name = %s" % name)
                tmpdir = name
                logger.info("forms.py ::: CookbookUpload - tmpdir = %s" % tmpdir)
                with open("%s/metadata.rb" % name) as metafile:
                    for line in metafile:
                        logger.debug("forms.py ::: CookbookUpload - line = %s" % line)
                        matchname = re.search(r'name[ \t]+(")?(\w+)(?(1)\1|)', line)
                        matchver  = re.search(r'version[ \t]+(")?([\d.]+)(?(1)\1|)', line)
                        if matchname:
                            cookbook_name = matchname.group(2)
                        elif matchver:
                            cookbook_ver  = matchver.group(2)
                            break
            
            if  not (cookbook_name and cookbook_ver):            
                raise ParseMetadataException()

            logger.debug("forms.py ::: CookbookUpload - cookbook_name = %s" % cookbook_name)
            logger.debug("forms.py ::: CookbookUpload - cookbook_ver = %s" % cookbook_ver)
            cookbook_path = uploadir + cookbook_name
            logger.debug("forms.py ::: CookbookUpload - cookbook_path = %s" % cookbook_path)
            shutil.move(tmpdir, cookbook_path)

        except urllib2.HTTPError as e:
                error = True
                self.created_msg(_('There was an error downloading zip file'), 'danger')
        except urllib2.URLError as e:
                error = True
                self.created_msg(_('There was an error downloading zip file'), 'danger')
        except zipfile.BadZipfile as e:
                error = True
                self.created_msg(_('File is not a zip file: %s') % zipped.name, 'danger')
        except OSError as e:
                error = True
                if e.errno == errno.EACCES:
                    self.created_msg(_('Permission denied: %s') % uploadir, 'danger')
                else:
                    logger.error("forms.py ::: CookbookUpload - Error = %s" % e.strerror)
                    self.created_msg(_('There was an error attempting to upload the cookbook. Please contact an administrator'), 'danger')
        except IOError as e:
                logger.debug("forms.py ::: CookbookUpload - e = %s" % e)
                error = True
                self.created_msg(_('No such file or directory: metadata.rb'), 'danger')
        except ParseMetadataException as e:
                logger.debug("forms.py ::: CookbookUpload - e = %s" % e)
                error = True
                self.created_msg(_('No cookbook name or version found in metadata.rb'),'danger')

        if not error:
            obj = {
                "_id": ObjectId(),
                "name": cookbook_name,
                "path": uploadir,
                "type": 'upload',
                "version": cookbook_ver
            }
            cookbook_upload.delay(self.request.user, 'upload', obj)
            logbook_link = '<a href="' +  self.request.application_url + '/#logbook' + '">' + _("here") + '</a>'
            self.created_msg(_("Upload cookbook enqueue. Visit logbook %s") % logbook_link)


class CookbookRestoreForm(GecosForm):
    def validate(self, data):
        data_dict = dict(data)
        return super(CookbookUploadForm, self).validate(data)

    def save(self, upload):
        self.created_msg(_('Restore cookbook.'))

class UpdateForm(GecosForm):
    def validate(self, data):
        data_dict = dict(data)
        logger.debug("forms.py ::: UpdateForm - data = {0}".format(data_dict))
        return super(UpdateForm, self).validate(data)
    def save(self, update):
        settings = get_current_registry().settings
        update = update['local_file'] if update['local_file'] is not None else update['remote_file']
        sequence = re.match('^update-(\w+)\.zip$', update['filename']).group(1)
        logger.debug("forms.py ::: UpdateForm - sequence = %s" % sequence)
        # Updates directory: /opt/gecoscc/updates/<sequence>
        updatesdir = settings['updates.dir'] + sequence
        logger.debug("forms.py ::: UpdateForm - updatesdir = %s" % updatesdir)
        # Update zip file
        zipped = settings['updates.tmp'] + update['filename']
        logger.debug("forms.py ::: UpdateForm - zipped = %s" % zipped)
        try:
            # https://docs.python.org/2/library/shutil.html
            # The destination directory, named by dst, must not already exist; it will be created as well as missing parent directories
            # Checking copytree NFS
            shutil.copytree(update['decompress'], updatesdir)
            shutil.rmtree(update['decompress'])
            # Move zip file to updates dir
            shutil.move(zipped, updatesdir)

            # Decompress cookbook zipfile
            cookbookdir = settings['updates.cookbook'].format(sequence)
            logger.debug("forms.py ::: UpdateForm - cookbookdir = %s" % cookbookdir)
            for cookbook in os.listdir(cookbookdir):
                cookbook = cookbookdir + os.sep + cookbook
                logger.debug("forms.py ::: UpdateForm - cookbook = %s" % cookbook)
                if zipfile.is_zipfile(cookbook):
                    zip_ref = zipfile.ZipFile(cookbook,'r')
                    zip_ref.extractall(cookbookdir + os.sep + settings['chef.cookbook_name'])
                    zip_ref.close()
            # Insert update register
            self.request.db.updates.insert({'_id': sequence, 'name': update['filename'], 'path': updatesdir, 'timestamp': int(time.time()), 'rollback':0, 'user': self.request.user['username']})
            # Launching task for script execution
            script_runner.delay(self.request.user, sequence)
            link = '<a href="' +  self.request.application_url + '/updates/tail/' + sequence + '/">' + _("here") + '</a>'
            self.created_msg(_("Update log. %s") % link)
        except OSError as e:
                error = True
                if e.errno == errno.EACCES:
                    self.created_msg(_('Permission denied: %s') % updatesdir, 'danger')
                else:
                    self.created_msg(_('There was an error attempting to upload an update. Please contact an administrator'), 'danger')
        except (IOError, os.error) as e:
            pass
        except errors.DuplicateKeyError as e:
            logger.error('Duplicate key error')
            self.created_msg(_('There was an error attempting to upload an update. Please contact an administrator'), 'danger')
class MaintenanceForm(GecosForm):
    css_class = 'deform-maintenance'

    def __init__(self, schema, request, *args, **kwargs):
        self.request = request
        buttons = (GecosButton(title=_('Submit'),
                               css_class='deform-maintenance-submit'),)

        super(MaintenanceForm, self).__init__(schema, buttons=buttons, *args, **kwargs)

    def save(self, postvars):
        logger.debug("forms.py ::: MaintenanceForm - postvars = {0}".format(postvars))

        if postvars['maintenance_message'] == "":
            logger.debug("forms.py ::: MaintenanceForm - Deleting maintenance message")
            self.request.db.settings.remove({'key':'maintenance_message'})
            self.created_msg(_('Maintenance message was deleted successfully.'))
        else:
            logger.debug("forms.py ::: MaintenanceForm - Creating maintenance message")
            compose = postvars['maintenance_message']
            maintenance_mode(self.request, compose)
            msg = self.request.db.settings.find_one({'key':'maintenance_message'})
            if msg is None:
                msg = {'key':'maintenance_message', 'value': compose, 'type':'string'}
                self.request.db.settings.insert(msg)
            else:
                self.request.db.settings.update({'key':'maintenance_message'},{'$set':{ 'value': compose}})

            self.request.session['maintenance_message'] = compose
            self.created_msg(_('Maintenance settings saved successfully.'))
