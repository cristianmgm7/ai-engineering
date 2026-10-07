"""L4 · Edges — channels in and out, HTTP ingress, workers.

``channels.py`` is the kernel interface. A product subpackage (``whatsapp/``,
next) holds that channel's ChannelAdapter and webhook, where the channel's own
names (WhatsApp ``wa_id``, chat id) are mapped onto the standard ones
(``session_id``, ``principal_id``).
"""
