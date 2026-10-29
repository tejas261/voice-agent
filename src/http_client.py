"""Verified HTTPS using the operating system trust store, including custom CAs."""

import os
import ssl

import truststore


def tls_context():
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cafile, capath = os.getenv("SSL_CERT_FILE"), os.getenv("SSL_CERT_DIR")
    if cafile or capath:
        context.load_verify_locations(cafile=cafile, capath=capath)
    return context
