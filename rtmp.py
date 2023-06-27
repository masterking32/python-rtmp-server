import asyncio
import logging
import amf

# Config
LogLevel = logging.INFO

# RTMP packet types
RTMP_TYPE_SET_CHUNK_SIZE = 1  # Set Chunk Size message (RTMP_PACKET_TYPE_CHUNK_SIZE 0x01) - The Set Chunk Size message is used to inform the peer about the chunk size for subsequent chunks.
RTMP_TYPE_ABORT = 2  # Abort message - The Abort message is used to notify the peer to discard a partially received message.
RTMP_TYPE_ACKNOWLEDGEMENT = 3  # Acknowledgement message (RTMP_PACKET_TYPE_BYTES_READ_REPORT 0x03) - The Acknowledgement message is used to report the number of bytes received so far.
RTMP_PACKET_TYPE_CONTROL = 4  # Control message - Control messages carry protocol control information between the RTMP peers.
RTMP_TYPE_WINDOW_ACKNOWLEDGEMENT_SIZE = 5  # Window Acknowledgement Size message (RTMP_PACKET_TYPE_SERVER_BW 0x05) - The Window Acknowledgement Size message is used to inform the peer about the window acknowledgement size.
RTMP_TYPE_SET_PEER_BANDWIDTH = 6  # Set Peer Bandwidth message (RTMP_PACKET_TYPE_CLIENT_BW 0x06) - The Set Peer Bandwidth message is used to inform the peer about the available outgoing bandwidth.
RTMP_TYPE_AUDIO = 8  # Audio data message (RTMP_PACKET_TYPE_AUDIO 0x08) - The Audio data message carries audio data.
RTMP_TYPE_VIDEO = 9  # Video data message (RTMP_PACKET_TYPE_VIDEO 0x09) - The Video data message carries video data.
RTMP_TYPE_FLEX_STREAM = 15  # Flex Stream message (RTMP_PACKET_TYPE_FLEX_STREAM_SEND 0x0F) - The Flex Stream message is used to send AMF3-encoded stream metadata.
RTMP_TYPE_FLEX_OBJECT = 16  # Flex Shared Object message (RTMP_PACKET_TYPE_FLEX_SHARED_OBJECT 0x10) - The Flex Shared Object message is used to send AMF3-encoded shared object data.
RTMP_TYPE_FLEX_MESSAGE = 17  # Flex Message message (RTMP_PACKET_TYPE_FLEX_MESSAGE 0x11) - The Flex Message message is used to send AMF3-encoded RPC or shared object events.
RTMP_TYPE_DATA = 18  # AMF0 Data message (RTMP_PACKET_TYPE_INFO 0x12) - The AMF0 Data message carries generic AMF0-encoded data.
RTMP_TYPE_SHARED_OBJECT = 19  # AMF0 Shared Object message (RTMP_PACKET_TYPE_INFO 0x12) - The AMF0 Shared Object message carries AMF0-encoded shared object data.
RTMP_TYPE_INVOKE = 20  # AMF0 Invoke message (RTMP_PACKET_TYPE_SHARED_OBJECT 0x13) - The AMF0 Invoke message is used for remote procedure calls (RPC) or command execution.
RTMP_TYPE_METADATA = 22  # Metadata message (RTMP_PACKET_TYPE_FLASH_VIDEO 0x16) - The Metadata message carries metadata related to the media stream.

class RTMPServer:
    def __init__(self, host='0.0.0.0', port=1935):
        # Socket
        self.host = host
        self.port = port
        self.client_ip = '0.0.0.0'

        # Log
        self.logger = logging.getLogger('RTMPServer')
        self.logger.setLevel(LogLevel)
        self.logger.addHandler(logging.StreamHandler())

        # RTMP
        self.chunk_size = 128  # Default chunk size
        self.window_acknowledgement_size = 5000000  # Default window acknowledgement size
        self.peer_bandwidth = 0  # Default peer bandwidth

        # RTMP Invoke Connect Data
        self.flashVer = 'FMLE/3.0 (compatible; FMSc/1.0)'
        self.connectType = 'nonprivate'
        self.tcUrl = ''
        self.swfUrl = ''
        self.app = ''

    async def handle_client(self, reader, writer):
        # Get client IP address
        self.client_ip  = writer.get_extra_info('peername')
        self.logger.info("New client connected: %s", self.client_ip)

        # Perform RTMP handshake
        try:
            await asyncio.wait_for(self.perform_handshake(reader, writer), timeout=5)
        except asyncio.TimeoutError:
            self.logger.error("Handshake timeout. Closing connection: %s", self.client_ip)
            writer.close()
            await writer.wait_closed()
            return

        # Process RTMP messages
        while True:
            try:
                chunk_data = await reader.read(self.chunk_size)
                if not chunk_data:
                    break  # Client disconnected
                rtmp_packet = self.parse_rtmp_packet(chunk_data)
                self.handle_rtmp_packet(rtmp_packet)

            except asyncio.TimeoutError:
                self.logger.error("Connection timeout. Closing connection: %s", self.client_ip)
                writer.close()
                await writer.wait_closed()
                return

        # Close the client connection
        writer.close()
        await writer.wait_closed()
        self.logger.info("Client disconnected: %s", self.client_ip)

    async def perform_handshake(self, reader, writer):
        # Read and echo C0C1
        c0c1_data = await asyncio.wait_for(reader.readexactly(1537), timeout=5)
        writer.write(c0c1_data)
        await writer.drain()

        # Read and echo C2
        c2_data = await asyncio.wait_for(reader.readexactly(1536), timeout=5)
        writer.write(c2_data)
        await writer.drain()
        self.logger.info("Handshake done!")

    def parse_rtmp_packet(self, chunk_data):
        # Parse RTMP packet
        fmt = (chunk_data[0] & 0b11000000) >> 6
        cid = chunk_data[0] & 0b00111111
        timestamp = int.from_bytes(chunk_data[1:4], byteorder='big')
        msg_length = int.from_bytes(chunk_data[4:7], byteorder='big')
        msg_type_id = chunk_data[7]
        msg_stream_id = int.from_bytes(chunk_data[8:12], byteorder='big')
        payload = chunk_data[12:]

        # Create rtmpPacket object
        rtmp_packet = {
            "header": {
                "fmt": fmt,
                "cid": cid,
                "timestamp": timestamp,
                "length": msg_length,
                "type": msg_type_id,
                "stream_id": msg_stream_id
            },
            "clock": 0,
            "payload": payload,
            "capacity": len(payload),
            "bytes": len(chunk_data)
        }

        return rtmp_packet

    def handle_rtmp_packet(self, rtmp_packet):
        # Handle RTMP packet
        self.logger.info("Received RTMP packet:")
        # self.logger.info("  RTMP Packet: %s", rtmp_packet)

        # Extract information from rtmp_packet and process as needed
        msg_type_id = rtmp_packet["header"]["type"]
        payload = rtmp_packet["payload"]

        if msg_type_id == RTMP_TYPE_SET_CHUNK_SIZE:
            self.handle_chunk_size_message(payload)
        elif msg_type_id == RTMP_TYPE_ACKNOWLEDGEMENT:
            self.handle_bytes_read_report(payload)
        # elif msg_type_id == RTMP_PACKET_TYPE_CONTROL:
        #     self.handle_control_message(payload)
        elif msg_type_id == RTMP_TYPE_WINDOW_ACKNOWLEDGEMENT_SIZE:
            self.handle_window_acknowledgement_size(payload)
        elif msg_type_id == RTMP_TYPE_SET_PEER_BANDWIDTH:
            self.handle_set_peer_bandwidth(payload)
        # elif msg_type_id == RTMP_TYPE_AUDIO:
        #     self.handle_audio_data(payload)
        # elif msg_type_id == RTMP_TYPE_VIDEO:
        #     self.handle_video_data(payload)
        # elif msg_type_id == RTMP_TYPE_FLEX_STREAM:
        #     self.handle_flex_stream_message(payload)
        # elif msg_type_id == RTMP_TYPE_FLEX_OBJECT:
        #     self.handle_flex_shared_object_message(payload)
        elif msg_type_id == RTMP_TYPE_FLEX_MESSAGE:
            self.handle_amf0_invoke_message(rtmp_packet)
        # elif msg_type_id == RTMP_TYPE_DATA:
        #     self.handle_amf0_data_message(payload)
        # elif msg_type_id == RTMP_TYPE_SHARED_OBJECT:
        #     self.handle_amf0_shared_object_message(payload)
        elif msg_type_id == RTMP_TYPE_INVOKE:
            self.handle_amf0_invoke_message(rtmp_packet)
        # elif msg_type_id == RTMP_TYPE_METADATA:
        #     self.handle_metadata_message(payload)
        else:
            self.logger.warning("Unsupported RTMP packet type: %s", msg_type_id)


    def handle_chunk_size_message(self, payload):
        # Handle Chunk Size message
        new_chunk_size = int.from_bytes(payload, byteorder='big')
        self.chunk_size = new_chunk_size
        self.logger.info("Updated chunk size: %d", self.chunk_size)
    
    def handle_bytes_read_report(self, payload):
        # Handle Acknowledgement message (Bytes Read Report)
        # bytes_read = int.from_bytes(payload, byteorder='big')
        # self.logger.info("Bytes read: %d", bytes_read)
        self.logger.info("RTMP_TYPE_ACKNOWLEDGEMENT")

    def handle_window_acknowledgement_size(self, payload):
        # Handle Window Acknowledgement Size message
        new_window_acknowledgement_size = int.from_bytes(payload, byteorder='big')
        self.window_acknowledgement_size = new_window_acknowledgement_size
        self.logger.info("Updated window acknowledgement size: %d", self.window_acknowledgement_size)

    def handle_set_peer_bandwidth(self, payload):
        # Handle Set Peer Bandwidth message
        bandwidth = int.from_bytes(payload[:4], byteorder='big')
        limit_type = payload[4]
        self.peer_bandwidth = bandwidth
        self.logger.info("Updated peer bandwidth: %d, Limit type: %d", self.peer_bandwidth, limit_type)

    def handle_amf0_invoke_message(self, rtmp_packet):
        offset = 1 if rtmp_packet['header']['type'] == RTMP_TYPE_FLEX_MESSAGE else 0
        payload = rtmp_packet['payload'][offset:rtmp_packet['header']['length']]
        amfReader = amf.AMF0(payload)
        inst = {}
        inst['type'] = rtmp_packet['header']['type']
        inst['time'] = rtmp_packet['header']['timestamp']
        inst['cmd'] = amfReader.read()  # first field is command name

        try:
            if rtmp_packet['header']['type'] == RTMP_TYPE_FLEX_MESSAGE or rtmp_packet['header']['type'] == RTMP_TYPE_INVOKE:
                inst['id'] = amfReader.read()  # second field *may* be message id
                inst['cmdData'] = amfReader.read()  # third is command data
                self.logger.info("Command Data %s", vars(inst['cmdData']))
            else:
                inst['id'] = 0
            inst['args'] = []  # others are optional
            while True:
                inst['args'].append(amfReader.read())  # amfReader.read()
        except EOFError:
            pass

        self.logger.info("Command %s", inst)

    async def start_server(self):
        server = await asyncio.start_server(
            self.handle_client, self.host, self.port)

        addr = server.sockets[0].getsockname()
        self.logger.info("RTMP server started on %s", addr)

        async with server:
            await server.serve_forever()

# Configure logging level and format
logging.basicConfig(level=LogLevel, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
rtmp_server = RTMPServer()
asyncio.run(rtmp_server.start_server())
