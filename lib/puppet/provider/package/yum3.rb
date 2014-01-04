require 'puppet/util/package'

Puppet::Type.type(:package).provide :yum3, :parent => :yum, :source => :rpm do
    desc "Support via `yum` with yum plugin."

    has_feature :versionable
    attr_accessor :latest_info
    commands :yum => "yum", :rpm => "rpm", :python => "python"

    def self.nvra_match(pkg, packages)
        n = pkg.name
        nv = '%s-%s' % [pkg.name, pkg.properties[:version]]
        nvr = '%s-%s-%s' % [pkg.name, pkg.properties[:version], pkg.properties[:release]]
        nvra = '%s-%s-%s.%s' % [pkg.name, pkg.properties[:version], pkg.properties[:release], pkg.properties[:arch]]
        na = '%s.%s' % [pkg.name, pkg.properties[:arch]]
        nva = '%s-%s.%s' % [pkg.name, pkg.properties[:version], pkg.properties[:arch]]
        [n, nv, nvr, nvra, na, nva].each do |check|
            if packages.include?(check) then
                return packages[check]
            end
        end
        return nil
    end

    def self.prefetch(packages)
        super
        if Puppet[:tags].length != 0 then
            notice("Skipping prefetch, --tags used")
            return
        end
        if Puppet[:noop] then
            return
        end
        notice("Prefetch start...")
        packages = packages.clone
        # Install our yum plugin
        yum = packages["yum"]
        cat = yum.catalog
        ['/usr/lib/yum-plugins/puppet.py', '/etc/yum/pluginconf.d/puppet.conf'].each do |file|
            rsc = cat.resource('File[%s]' % file)
            rsc.retrieve
            if not rsc.exist? or rsc[:content] != '{md5}' + rsc.md5_file(file) then
                rsc.write('prefetch')
            end
        end
        installed = []
        depsolved = []
        # Filter out things that don't need any action
        self.instances.each do |pkg|
            pkg2 = self.nvra_match(pkg, packages)
            next unless pkg2
            ens = pkg2[:ensure].to_s
            v = pkg.properties[:version]
            vr = '%s-%s' % [pkg.properties[:version], pkg.properties[:release]]
            va = '%s.%s' % [pkg.properties[:version], pkg.properties[:arch]]
            vra = '%s-%s.%s' % [pkg.properties[:version], pkg.properties[:release], pkg.properties[:arch]]

            if ['installed', 'present', v, vr, vra, va].include?(ens) then
                depsolved.push(pkg2.title)
                depsolved.push(pkg.name)
                depsolved.push(pkg2[:name])
                packages.delete(pkg2.title)
            elsif ens == 'latest' and pkg2.provider.latest_info == nil then
                depsolved.push(pkg2.title)
                depsolved.push(pkg.name)
                depsolved.push(pkg2[:name])
                packages.delete(pkg2.title)
            elsif ['absent', 'purged'].include?(ens) then
                installed.push(pkg2.title)
                depsolved.push(pkg2.title)
                depsolved.push(pkg.name)
                depsolved.push(pkg2[:name])
            end
        end
        preinstall = []
        preinstallcount = -1
        while preinstall.count != preinstallcount do
            preinstallcount = preinstall.count
            packages.clone.each do |name,package|
                name_normalized = name.gsub(/-\d.*/, '')
                if depsolved.include?(name) or depsolved.include?(name_normalized) then
                    packages.delete(name)
                    next
                end
                if ['absent', 'purged'].include?(package[:ensure].to_s) and not installed.include?(package.name) then
                    depsolved.push(package.name)
                    packages.delete(name)
                    next
                end
                deps = package.catalog.relationship_graph.dependencies(package)
                deps.clone.each do |dep|
                    # Ignore whits (Classes, Stages and other internal noop resources)
                    if dep.type.to_s == 'whit' then
                         deps.delete(dep)
                         next
                    end
                    # If the dependency is a package we will also install: good!
                    if dep.type.to_s == 'package' and depsolved.include?(dep.name) then
                        deps.delete(dep)
                        next
                    end
                    # If the dependency is a file in /etc/yum or /usr/lib/yum/plugins: our plugin will do them
                    if dep.type.to_s == 'file' then
                        if dep[:path] =~ /^(\/etc\/yum|\/usr\/lib\/yum-plugins)/ then
                            deps.delete(dep)
                            next
                        end
                    end
                end
                if deps.count == 0 then
                    preinstall.push(package)
                    depsolved.push(package.name)
                    packages.delete(name)
                end
            end
        end
        if preinstall.count == 0 then
            if packages.keys.count != 0 then
                notice("Prefetch done (unable to process %d packages)..." % packages.keys.count)
            else
                notice("Prefetch done...")
            end
            return
        end
        notice("Processing %d/%d packages in one transaction" % [preinstall.count, preinstall.count + packages.keys.count])
        notice(preinstall.to_s)
        pkgs = []
        preinstall.each do |pkg|
            ens = pkg[:ensure].to_s
            if ['absent', 'purged'].include?(ens) then
                pkgs.push('~' + pkg[:name])
            elsif ['installed', 'latest', 'present'].include?(ens) then
                pkgs.push(pkg[:name])
            else
                pkgs.push(pkg[:name] + '-' + ens)
            end
        end
        output = yum "-d", "0", "-e", "0", "-y", "install-remove", *pkgs
        notice("Prefetch done...")
    end
end
