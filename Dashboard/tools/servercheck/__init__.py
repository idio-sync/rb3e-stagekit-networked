"""Checks that drive a running rb3server over the network.

Two components with different access requirements. The probe speaks only the
network protocols and can be pointed at any reachable deployment, including the
Unraid host during the pilot. The controller owns a server's lifecycle -- it
starts a process or a container, signals it, and reads its data directory -- so
it needs local process or Docker access and never runs against the pilot host.

This package lives under tools/ rather than tests/ deliberately. The desktop
build job runs `unittest discover -s tests` on three operating systems, and
these checks need a running server, which that job has no way to provide.
"""
