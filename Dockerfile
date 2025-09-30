FROM ubuntu:22.04

ENV container docker
ENV DEBIAN_FRONTEND=noninteractive

# Systemd aur SSH install
RUN apt-get update && \
    apt-get install -y systemd systemd-sysv dbus dbus-user-session \
    openssh-server sudo curl iproute2 iputils-ping vim nano && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# SSH setup
RUN mkdir -p /var/run/sshd && \
    echo 'root:Docker@123' | chpasswd && \
    sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config

# Systemd ke liye required mounts
VOLUME [ "/sys/fs/cgroup" ]

# Init process
CMD ["/sbin/init"]
