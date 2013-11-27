Close puppet-yum integration
============================

By default, yum and puppet interact in a rather suboptimal way. This project
aims to improve that drastically by making them much more aware of each other.
Using a custom puppet provider for packages, and a yum plugin for puppet
interaction, puppet runs become more reliable and (when using lots of rpms) a
lot faster as well.

How do you use it?
------------------
First copy this directory over to your `modules` directory. Then add this to
`site.pp`:

    Package{ provider => 'yum3' }

You also need to make sure the module is actually used, or else the yum plugin
will not be installed. A last prerequisite is that the yum package should be
managed with puppet, so the provider can get a grip on the catalog.

As an example, here is how I configure it:
 * Every server gets a role-based class assigned in puppet.
 * Each of these classes include the lass `base::common` as baseline configuration. 
 * `base::common` includes the yum and yum-plugin-puppet classes
 * The yum class manages yum: `package{"yum": ensure => latest}`

This looks like the following:

manifests/site.pp

    ...
    include "nodes.pp"
    ...

manifests/nodes.pp

    ...
    node some-server.example.com {
        ...
        include s_example_service
    }
    ...

modules/s\_example\_service/manifests/init.pp

    class s_example_service {
        ...
        include base::common
    }

modules/base/manifests/init.pp

    class base::common {
        ...
        include yum
        include yum-plugin-puppet
        ...
    }

modules/yum/manifests/init.pp

    class yum {
        ...
        package{"yum":
            ensure => latest;
        }
        ...
    }


What does it change?
--------------------
The biggest change is in the way puppet installs packages. Instead of doing
them one by one, it installs/removes most packages in one call to yum. At the
start of a puppet run, the provider walks the dependency graph and collects a
list of packages that have no puppet dependencies other than packages that
don't have any dependencies (etc...). It then calls yum once to install/remove
all of these.

Besides speeding up puppet runs, this also avoids version flapping. Imagine the
following manifest:

    package{"some-package":
        ensure => latest
    }
    package{"some-other-package":
        ensure => "1.0-1"
    }

If the latest version of `some-package` has an RPM dependency on version 2.0 of
`some-other-package`, alternate puppet runs will keep upgrading and downgrading
`some-other-package`. With this provider that's impossible.

The provider will also install the yum plugin and its configuration, bypassing
the dependency graph.

The yum plugin changes a few things:

* It adds an install-remove command, so you can install and remove packages in
  one call; this is used by the package provider. For example `yum
  install-remove ~foo bar` will remove `foo` and add `bar`.
* It reads puppet's catalog and uses that to make decisions about which
  packages to ignore (wrong versions and packages configured with `ensure =>
  absent` or `ensure => purged`). This also helps with the version flapping
  problem described above.
* Based on the puppet catalog it will refuse to remove certain packages, again
  to help with rpm dependencies not matching puppet dependencies.
* When installing packages that will overwrite puppet-managed files, it will
  warn you about this and ask for confirmation.

TODO
----
* Puppet still calls rpm very frequently for version info, we should be smarter
  and avoid that.
