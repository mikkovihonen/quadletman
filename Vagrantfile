# -*- mode: ruby -*-
# vi: set ft=ruby :
#
# Fedora smoke-test VM for quadletman RPM packages.
# See docs/testing.md for full instructions.
#
# Usage:
#   vagrant up                  # provision + smoke test
#   vagrant provision           # re-run provisioner (rebuild + reinstall)
#   vagrant rsync && vagrant provision   # push code changes then re-test
#   vagrant ssh                 # shell into the VM
#   vagrant destroy -f          # tear down

Vagrant.configure("2") do |config|
  config.vm.box      = "fedora/41-cloud-base"
  config.vm.hostname = "quadletman-smoke"

  # Forward the app port so the UI is reachable from the host browser
  config.vm.network "forwarded_port", guest: 8000, host: 8001, host_ip: "127.0.0.1"

  # libvirt provider — preferred on Linux bare-metal
  config.vm.provider "libvirt" do |lv|
    lv.memory = 2048
    lv.cpus   = 2
  end

  # VirtualBox provider — fallback for Windows/macOS hosts (including WSL2)
  config.vm.provider "virtualbox" do |vb|
    vb.name   = "quadletman-smoke"
    vb.memory = 2048
    vb.cpus   = 2
    vb.customize ["modifyvm", :id, "--ioapic", "on"]
  end

  # Sync project source into the VM via rsync (excludes build artefacts and .git)
  config.vm.synced_folder ".", "/vagrant/quadletman",
    type: "rsync",
    rsync__exclude: [
      ".git/",
      ".venv/",
      "__pycache__/",
      "*.egg-info/",
      "dist/",
      "build/",
      "node_modules/",
    ]

  config.vm.provision "shell", path: "packaging/smoke-test-vm.sh"
end
