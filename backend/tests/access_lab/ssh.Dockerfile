FROM alpine:3.22
RUN apk add --no-cache openssh-server sudo && \
    adduser -D lab_reader && adduser -D lab_operator && \
    echo 'lab_reader:lab_reader_test_password' | chpasswd && \
    echo 'lab_operator:lab_operator_test_password' | chpasswd && \
    ssh-keygen -A && mkdir -p /opt/privileged && \
    echo 'lab_operator ALL=(root) NOPASSWD: /usr/bin/touch /opt/privileged/approved' > /etc/sudoers.d/lab_operator && \
    chmod 440 /etc/sudoers.d/lab_operator
RUN ln -s /bin/touch /usr/bin/touch
CMD ["/usr/sbin/sshd", "-D", "-e", "-o", "PasswordAuthentication=yes", "-o", "PermitRootLogin=no"]
