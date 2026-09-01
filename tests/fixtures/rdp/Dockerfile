FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
        dbus-x11 \
        sudo \
        xfce4-session \
        xfce4-panel \
        xfce4-terminal \
        xfdesktop4 \
        xorgxrdp \
        xrdp \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash deebee \
    && echo 'deebee:deebee-secret' | chpasswd \
    && usermod -aG sudo,ssl-cert deebee \
    && printf 'startxfce4\n' > /home/deebee/.xsession \
    && chown deebee:deebee /home/deebee/.xsession

EXPOSE 3389

CMD ["/bin/bash", "-lc", "mkdir -p /run/xrdp && /usr/sbin/xrdp-sesman && exec /usr/sbin/xrdp --nodaemon"]
