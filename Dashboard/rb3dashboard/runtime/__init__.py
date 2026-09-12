"""Headless runtime services shared by the server entry point.

Modules in this package must not import Tk, VLC, Discord, or display
dependencies, so the server can run on a host with no desktop session.
"""
