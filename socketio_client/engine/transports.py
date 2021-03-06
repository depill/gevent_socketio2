import time
import datetime
import urllib
import urlparse
import gevent
from gevent.event import Event
import requests
from socketio.engine import parser
from socketio.event_emitter import EventEmitter
import logging
from ws4py.client.geventclient import WebSocketClient

logger = logging.getLogger(__name__)


class Transport(EventEmitter):
    protocol_version = 3
    name = '_base_transport'

    def __init__(self, path, host, port, secure=False, query=None, agent=None, force_base64=False):
        self.path = path
        self.hostname = host
        self.port = port
        self.secure = secure
        self.query = query
        self.ready_state = None
        self.agent = agent
        self.writable = False
        self.force_base64 = force_base64
        self.supports_binary = not self.force_base64

        self.sid = None

        super(Transport, self).__init__()

    def on_error(self, msg, desc):
        self.emit("error", msg, desc)

    def open(self):
        if 'closed' == self.ready_state or self.ready_state is None:
            self.ready_state = 'opening'
            self.do_open()

    def close(self):
        if 'opening' == self.ready_state or 'open' == self.ready_state:
            self.do_close()
            self.on_close()

    def send(self, packets):
        if 'open' == self.ready_state:
            if type(packets) not in (list, tuple):
                packets = [packets]
            self.write(packets)

        else:
            raise ValueError("Transport not open")

    def write(self, packets):
        raise NotImplementedError()

    def on_open(self):
        self.ready_state = 'open'
        self.writable = True
        self.emit('open')

    def on_data(self, data):
        packet = parser.Parser.decode_packet(data)
        self.on_packet(packet)

    def on_packet(self, packet):
        self.emit('packet', packet)

    def on_close(self):
        self.ready_state = 'closed'
        self.emit('close')

    def do_close(self):
        # FIXME subclass not implement this
        raise NotImplementedError()

    def do_open(self):
        raise NotImplementedError()


class PollingTransport(Transport):
    name = "polling"

    def __init__(self, *args, **kwargs):
        self.polling = False
        super(PollingTransport, self).__init__(*args, **kwargs)

    def pause(self, nowait=False, timeout=30):
        """
        Pause polling
        :param nowait: bool
        :return:
        """
        self.ready_state = 'pausing'
        context = {"total": 0}

        if not nowait:
            context['event'] = Event()

        def pause():
            logger.debug("paused")
            self.ready_state = 'paused'

            if 'event' in context:
                context['event'].set()

        if self.polling or not self.writable:
            context["total"] = 0

            def on_poll_complete():
                    logger.debug("pre-pause polling complete")
                    context["total"] -= 1

                    if not context["total"]:
                        pause()

            if self.polling:
                logger.debug("we are currently polling - waiting to pause")
                context["total"] += 1
                self.once("poll_complete", on_poll_complete)

            if not self.writable:
                logger.debug("we are currently writing - waiting to pause")
                context["total"] += 1
                self.once("drain", on_poll_complete)
        else:
            pause()

        if not nowait:
            paused = context['event'].wait(timeout=timeout)
            if paused:
                return
            else:
                raise RuntimeWarning("The pause timeout")

    def poll(self):
        logger.debug("polling")
        self.polling = True
        self.do_poll()
        self.emit("poll")

    def do_poll(self):
        raise NotImplementedError()

    def do_open(self):
        """
        All polling transport needs do a poll to get handshake packet back
        :return:
        """
        self.poll()

        while self.ready_state == 'open':
            self.poll()

    def on_data(self, data):
        logger.debug("polling got data %s", data)

        for packet, index, total in parser.Parser.decode_payload(data):
            if 'opening' == self.ready_state:
                self.on_open()

            if 'close' == packet['type']:
                self.on_close()
                return False

            # bypass and handle the message
            self.on_packet(packet)

        if 'open' == self.ready_state and self.sid is None:
            raise ValueError("sid is none after on_open, forgot setting sid in engine_socket?")

        if 'closed' != self.ready_state:
            self.polling = False
            self.emit("poll_complete")

            if 'open' == self.ready_state:
                # Return to let outer loop call next poll
                return True
            else:
                logger.debug('ignoring polling - transport state "%s"', self.ready_state)

    def on_close(self):
        """
        Send a close packet
        :return:
        """
        def close():
            logger.debug('writing close packet')
            self.write([{"type": "close"}])

        if 'open' == self.ready_state:
            logger.debug("transport open - closing")
            close()
        else:
            logger.debug("transport not open - deferring close")
            self.once("open", close)

    def write(self, packets):
        self.writable = False
        encoded = parser.Parser.encode_payload(packets, self.supports_binary)
        self.do_write(encoded)
        self.writable = True
        self.emit('drain')

    def do_write(self, data):
        raise NotImplementedError()

    def uri(self):
        schema = 'https' if self.secure else 'http'
        port = ''
        query = {
            'EIO': self.protocol_version,
            'transport': self.name,
            't': time.mktime(datetime.datetime.now().timetuple()) * 1000
        }

        if self.sid is not None:
            query['sid'] = self.sid

        if not self.supports_binary and self.sid is None:
            query["b64"] = 1

        query = urllib.urlencode(query)

        if self.port is not None and (('https' == schema and self.port != 443) or ('http' == schema and self.port != 80)):
            port = ':' + str(self.port)

        if len(query) > 0:
            query = '?' + query

        return urlparse.urljoin(schema + '://' + self.hostname + port, self.path) + query


class XHRPollingTransport(PollingTransport):
    def __init__(self, *args, **kwargs):
        super(XHRPollingTransport, self).__init__(**kwargs)

        self.data_response = None
        self.poll_response = None

    def do_write(self, data):
        response = self.request(method='POST', data=data)

        if 300 > response.status_code >= 200:
            return
        else:
            self.on_error('xhr request failed', response.content)

    def do_poll(self):
        logger.debug('xhr poll')
        response = self.request()

        if 300 > response.status_code >= 200:
            self.on_load(response)
        elif response.status_code >= 400:
            self.on_error('xhr request failed', response.content)

    def request(self, data=None, method='GET'):
        """
        :param data: The data to be send
        :param method: GET or POST
        :return:
        """

        content_type = None
        if method == 'POST':
            # TODO Use the has_bin func to check?
            is_binary = type(data) is bytearray
            if is_binary:
                content_type = "application/octet-stream"
            else:
                content_type = "text/plain;charset=UTF-8"

        if method == 'GET':
            request_func = requests.get
        else:
            request_func = requests.post

        uri = self.uri()
        return request_func(uri, data=data, headers={"content-type": content_type})

    def on_load(self, response):
        content_type = response.headers["content-type"]
        if content_type == 'application/octet-stream':
            data = bytearray(response.content)
        else:
            if not self.supports_binary:
                data = response.content
            else:
                data = 'ok'

        self.on_data(data)


class WebsocketTransport(Transport):
    name = 'websocket'

    def __init__(self, *args, **kwargs):
        super(WebsocketTransport, self).__init__(*args, **kwargs)
        self.websocket = None
        self.read_job = None

    def do_open(self):
        url = self.uri()
        self.websocket = WebSocketClient(url)
        self.websocket.connect()
        self.on_open()
        self.read_job = gevent.spawn(self._read)

    def _read(self):
        while True:
            msg = self.websocket.receive()
            if msg is not None:
                if msg.is_text:
                    self.on_data(str(msg.data))
                else:
                    self.on_data(bytearray(msg.data))
            else:
                break

    def write(self, packets):
        self.writable = False

        for packet in packets:
            encoded_packet = parser.Parser.encode_packet(packet, self.supports_binary)
            try:
                binary = type(encoded_packet) is bytearray
                self.websocket.send(encoded_packet, binary=binary)
            except RuntimeError, e:
                self.on_error('The websocket clsoed without a close packet (%s)', e)
        self.on_drain()

    def on_drain(self):
        self.writable = True
        self.emit('drain')

    def uri(self):
        schema = 'wss' if self.secure else 'ws'

        port = ''
        if self.port and ((
            'wss' == schema and self.port != 433
                          ) or (
            'ws' == schema and self.port != 80
        )):
            port = ':' + str(self.port)

        query = {
            'EIO': self.protocol_version,
            'transport': self.name,
            't': time.mktime(datetime.datetime.now().timetuple()) * 1000,
        }

        if self.sid:
            query['sid'] = self.sid

        if not self.supports_binary:
            query['b64'] = 1

        query = '?' + urllib.urlencode(query)

        return schema + '://' + self.hostname + port + '/' + self.path.lstrip('/') + query

available_transports = {
    XHRPollingTransport.name: XHRPollingTransport,
    WebsocketTransport.name: WebsocketTransport
}