class yum_plugin_puppet {
    file { "/etc/yum/pluginconf.d/puppet.conf":
        mode    => 0644,
        owner   => root,
        group   => root,
        ensure  => file,
        source  => "puppet:///modules/yum_plugin_puppet/puppet.conf",
    }
    file { "/usr/lib/yum-plugins/puppet.py":
        mode    => 0644,
        owner   => root,
        group   => root,
        ensure  => file,
        source  => "puppet:///modules/yum_plugin_puppet/puppet.py",
    }
}
