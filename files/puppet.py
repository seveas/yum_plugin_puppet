# This file is maintained by puppet
# modules/yum_plugin_puppet/files/puppet.py

from yum.constants import *
from yum.plugins import TYPE_CORE, TYPE_INTERACTIVE, PluginYumExit
from yum import config
import socket
import rpm
import yum
import os
import re
try:
    import json
except ImportError:
    import simplejson as json

from yumcommands import YumCommand, checkRootUID, checkGPGKey, checkPackageArg

requires_api_version = '2.4'
plugin_type = (TYPE_CORE, TYPE_INTERACTIVE)

me = socket.gethostname()
if '.' not in me:
    me = socket.getfqdn()
puppet_catalog = '/var/lib/puppet/client_yaml/catalog/%s.yaml' % me
if not os.path.exists(puppet_catalog):
    # Try the json-serialized version
    puppet_catalog = '/var/lib/puppet/client_data/catalog/%s.json' % me
puppet_files = []
puppet_packages = {}
ABSENT = -1
PURGED = -2
OTHERVER = -3

def config_hook(conduit):
    global puppet_files
    global puppet_catalog
    global puppet_packages
    puppet_catalog = conduit.confString('main', 'puppet_catalog', default=puppet_catalog)
    if not os.path.exists(puppet_catalog):
        conduit.info(2, "Catalog file %s does not exist" % puppet_catalog)
        conduit.registerCommand(FakeInstallRemoveCommand())
        return

    conduit.info(2, "Looking at puppet catalog for repo info")
    if puppet_catalog.endswith('.yaml'):
        # PyYAML is far, far too slow to load the manifest. We'll make do with a regex.
        # Oh, and it can't parse it completely anyway. But it does take over a
        # minute to realize that. Parsing with a regex takes 0.01 seconds
        puppet_yaml = open(puppet_catalog).read()
        resource_re = r'(?<=\n)    - &id[^\n]*Relationship.*?(?=\n    [^ ])'

        packages = [dict(re.findall('(title|ensure):\s*"?(.*?)"?(?:\n|$)', pkg)) for pkg in
                    re.findall(resource_re, puppet_yaml, flags=re.DOTALL) if 'type: Package' in pkg]

        puppet_packages = dict([(pkg['title'], pkg.get('ensure', 'installed')) for pkg in packages])

        files = [dict(re.findall('(title|(?<=ruby/sym )target|content):\s*"?(.*?)"?(?:\n|$)', pkg)) for pkg in
                 re.findall(resource_re, puppet_yaml, flags=re.DOTALL) if 'type: File' in pkg]
        puppet_files = [x['title'] for x in files if 'title' in x and 'content' in x]
    else:
        catalog = json.load(open(puppet_catalog))
        files = []
        for resource in catalog['data']['resources']:
            if resource['type'] == 'File':
                title = resource['parameters'].get('target', resource['title'])
                if 'content' in resource['parameters']:
                    files.append({'title': title, 'content': resource['parameters']['content']})
                    puppet_files.append('title')
            elif resource['type'] == 'Package':
                puppet_packages[resource['title']] = resource['parameters'].get('ensure', 'installed')

    for f in files:
        if 'target' in f:
            f['title'] = f['target']
        if 'content' not in f:
            continue
        if not f['title'].startswith('/etc/yum') and not f['title'].startswith('/usr/lib/yum-plugins'):
            continue
        fn, content = f['title'], f['content'].decode("string_escape")
        content = content.replace('\\n', '\n')
        replace = False
        if not os.path.exists(fn):
            conduit.info(2, "Installing puppet-generated %s" % fn)
            replace = True
        else:
            fd = open(fn)
            old = fd.read()
            fd.close()
            if old != content:
                conduit.info(2, "Replacing %s with puppet-generated content" % fn)
                replace = True
        if replace:
            fd = open(fn, 'w')
            fd.write(content)
            fd.close()
    conduit.registerCommand(InstallRemoveCommand())
    parser = conduit.getOptParser()
    if parser:
        parser.add_option('--allow-removal', dest='allow_removal', action='append', metavar='PKG',
                          help="Allow removal of PKG, even if puppet says otherwise")

def nvra_match(po, pkgs):
    n = po.name
    v = po.version
    nv = '%s-%s' % (po.name, po.version)
    nvr = '%s-%s-%s' % (po.name, po.version, po.release)
    vr = '%s-%s' % (po.version, po.release)
    nvra = '%s-%s-%s.%s' % (po.name, po.version, po.release, po.arch)
    na = '%s.%s' % (po.name, po.arch)
    nva = '%s-%s.%s' % (po.name, po.version, po.arch)
    vra = '%s-%s.%s' % (po.version, po.release, po.arch)
    for check in (n, nv, nvr, nvra, na, nva):
        if check in pkgs:
            if pkgs[check] == 'absent':
                return ABSENT, pkgs[check]
            if pkgs[check] == 'purged':
                return PURGED, pkgs[check]
            if pkgs[check] not in (v, vr, vra, 'installed', 'abent', 'latest', 'present'):
                return OTHERVER, pkgs[check]
            return True, pkgs[check]
    return False, None

def exclude_hook(conduit):
    global puppet_packages
    opts, args = conduit.getCmdLine()
    if args[0] not in ('remove', 'purge', 'clean'):
        towarn = dict([(x, 'installed') for x in args[1:]])
    else:
        towarn = []
    allrepos = conduit.getRepos().listEnabled()
    count = 0
    for repo in allrepos:
        for po in conduit.getPackages(repo):
            loglevel = nvra_match(po, towarn)[0] and 2 or 3
            res, ver = nvra_match(po, puppet_packages)
            if res == ABSENT:
                conduit.info(loglevel," --> %s from %s excluded (puppet ensure => absent)" % (po, po.repoid))
                conduit.delPackage(po)
                count += 1
            elif res == PURGED:
                conduit.info(loglevel," --> %s from %s excluded (puppet ensure => purged)" % (po, po.repoid))
                conduit.delPackage(po)
                count += 1
            elif res == OTHERVER:
                conduit.info(loglevel," --> %s from %s excluded (puppet wants version %s instead)" % (po, po.repoid, ver))
                conduit.delPackage(po)
                count += 1
    if count:
        conduit.info(2, '%d packages excluded based on the puppet manifest' % count)

class InstallRemoveCommand(YumCommand):
    def getNames(self):
        return ['install-remove']

    def getUsage(self):
        return "[~]PACKAGE..."

    def getSummary(self):
        return "Install or remove packages on your system"

    def doCheck(self, base, basecmd, extcmds):
        checkRootUID(base)
        checkGPGKey(base)
        checkPackageArg(base, basecmd, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        self.doneCommand(base, "Setting up Install/Remove Process")
        self.doneCommand(base, str(extcmds))
        remove = [x[1:] for x in extcmds if x.startswith('~')]
        install = [x for x in extcmds if not x.startswith('~')]
        retmsgs = []
        try:
            ret, msgs = base.erasePkgs(remove)
            if ret != 0:
                retmsgs += msgs
        except yum.Errors.YumBaseError, e:
            return 1, [str(e)]
        try:
            for pkg in install:
                ret, msgs = base.installPkgs([pkg])
                if ret == 0:
                    # Install didn't work, try downgrade if it looks like
                    # version and release are specified
                    if re.search(r'-\d.*-\d', pkg):
                        ret, msgs = base.downgradePkgs([pkg])
                        if ret != 0:
                            retmsgs += msgs
                else:
                    retmsgs += msgs
        except yum.Errors.YumBaseError, e:
            return 1, [str(e)]
        if retmsgs:
            return 2, retmsgs
        else:
            return 0, ["No Packages marked for install/removal/downgrade"]

    def needTs(self, base, basecmd, extcmds):
        return True

    def needTsRemove(self, base, basecmd, extcmds):
        return True

class FakeInstallRemoveCommand(YumCommand):
    def getNames(self):
        return ['install-remove']

    def getUsage(self):
        return "[~]PACKAGE..."

    def getSummary(self):
        return "Install or remove packages on your system"

    def doCommand(self, base, basecmd, extcmds):
        return 1, ["Unable to use install-remove, puppet catalog not found"]

    def needTs(self, base, basecmd, extcmds):
        return False

    def needTsRemove(self, base, basecmd, extcmds):
        return False

def postresolve_hook(conduit):
    global puppet_packages
    ts = conduit.getTsInfo()
    opts, args = conduit.getCmdLine()
    exit = False
    for pkgs in ts.pkgdict.values():
        for pkg in pkgs:
            if pkg.ts_state == 'e' and nvra_match(pkg, puppet_packages)[0] not in (ABSENT, False):
                abort = True
                for pkgs2 in ts.pkgdict.values():
                    for pkg2 in pkgs2:
                        if pkg.name == pkg2.name and pkg2.ts_state != 'e':
                            # This is a downgrade
                            abort = False
                            break
                if abort and pkg.name not in (opts.allow_removal or []):
                    conduit.info(2, "Cannot delete package %s, it's required by puppet" % pkg.name)
                    exit = True
    if exit:
        raise PluginYumExit('')

def pretrans_hook(conduit):
    ts = conduit.getTsInfo()
    packages = []
    for attr in ('updated', 'installed', 'depinstalled', 'depupdated', 'reinstalled', 'downgraded'):
        packages += getattr(ts, attr, [])
    prompt = []
    for p in packages:
        for file in p.po.returnHeaderFromPackage().fiFromHeader():
            (file, size, mode, mtime, flags, dev, inode, link, state, vflags, user, group, csum) = file
            if file in puppet_files and not flags & rpm.RPMFILE_NOREPLACE:
                prompt.append("Installing %s overwrites puppet-managed file %s" % (str(p.po), file))
    if prompt:
        if not conduit.promptYN('\n'.join(prompt)):
            raise PluginYumExit('Aborting')
