import asyncio
import logging
import amf
import common
import struct
from typing import Optional

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
 
RTMP_CHUNK_TYPE_0 = 0 # 11-bytes: timestamp(3) + length(3) + stream type(1) + stream id(4)
RTMP_CHUNK_TYPE_1 = 1 # 7-bytes: delta(3) + length(3) + stream type(1)
RTMP_CHUNK_TYPE_2 = 2 # 3-bytes: delta(3)
RTMP_CHUNK_TYPE_3 = 3 # 0-byte

PING_SIZE, DEFAULT_CHUNK_SIZE, HIGH_WRITE_CHUNK_SIZE, PROTOCOL_CHANNEL_ID = 1536, 128, 4096, 2

class DisconnectClientException(Exception):
    pass

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
        self.out_chunk_size = 4096 # Default out chunk size
        self.window_acknowledgement_size = 5000000  # Default window acknowledgement size
        self.peer_bandwidth = 0  # Default peer bandwidth

        # RTMP Invoke Connect Data
        self.flashVer = 'FMLE/3.0 (compatible; FMSc/1.0)'
        self.connectType = 'nonprivate'
        self.tcUrl = ''
        self.swfUrl = ''
        self.app = ''
        self.objectEncoding = 0

        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        
        self.lastWriteHeaders = dict()
        self.nextChannelId = PROTOCOL_CHANNEL_ID + 1

    async def handle_client(self, reader, writer):
        # Get client IP address
        self.reader = reader
        self.writer = writer
        
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
                await self.handle_rtmp_packet(rtmp_packet)

            except asyncio.TimeoutError:
                self.logger.error("Connection timeout. Closing connection: %s", self.client_ip)
                writer.close()
                await writer.wait_closed()
                return
            
            except DisconnectClientException:
                self.logger.info("Disconnecting client: %s", self.client_ip)
                writer.close()
                await writer.wait_closed()
                return
            
            except Exception as e:
                self.logger.error("An error occurred: %s", str(e))
                self.logger.info("Disconnecting client: %s", self.client_ip)
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
        if fmt == RTMP_CHUNK_TYPE_0:
            cid = chunk_data[0] & 0b00111111
            timestamp = int.from_bytes(chunk_data[1:4], byteorder='big')
            msg_length = int.from_bytes(chunk_data[4:7], byteorder='big')
            msg_type_id = chunk_data[7]
            msg_stream_id = int.from_bytes(chunk_data[8:12], byteorder='big')
            payload = chunk_data[12:]
        elif fmt == RTMP_CHUNK_TYPE_1:
            cid = 0
            timestamp = int.from_bytes(chunk_data[1:4], byteorder='big')
            msg_length = int.from_bytes(chunk_data[4:7], byteorder='big')
            msg_type_id = chunk_data[7]
            msg_stream_id = 0
            payload = chunk_data[8:]
        elif fmt == RTMP_CHUNK_TYPE_2:
            # Handle Type 2 chunk
            cid = 0  # Assuming the same chunk stream ID as the previous chunk
            timestamp = int.from_bytes(chunk_data[1:4], byteorder='big')
            msg_length = 0  # No new message length
            msg_type_id = 0  # No new message type ID
            msg_stream_id = 0  # No new message stream ID
            payload = chunk_data[4:]  # Exclude the chunk header
        elif fmt == RTMP_CHUNK_TYPE_3:
            # Handle Type 3 chunk
            # No need to include any header information
            self.logger.info("FMT 3!")
            return
        else:
            self.logger.info("Unsupported FMT packet!")
            return

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
            "bytes": len(chunk_data),
            "chunk": chunk_data
        }

        return rtmp_packet

    async def handle_rtmp_packet(self, rtmp_packet):
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
            invoke_message = self.parse_amf0_invoke_message(rtmp_packet)
            await self.handle_invoke_message(invoke_message)
        # elif msg_type_id == RTMP_TYPE_DATA:
        #     self.handle_amf0_data_message(payload)
        # elif msg_type_id == RTMP_TYPE_SHARED_OBJECT:
        #     self.handle_amf0_shared_object_message(payload)
        elif msg_type_id == RTMP_TYPE_INVOKE:
            invoke_message = self.parse_amf0_invoke_message(rtmp_packet)
            await self.handle_invoke_message(invoke_message)
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

    async def handle_invoke_message(self, invoke):
        if invoke['cmd'] == 'connect':
            await self.handle_connect_command(invoke)
        else:
            self.logger.info("Unsupported invoke command %s!", invoke['cmd'])

    async def handle_connect_command(self, invoke):
        if hasattr(invoke['cmdData'], 'app'):
            self.app = invoke['cmdData'].app

        if self.app == '':
            self.logger.info("Empty 'app' attribute. Disconnecting client: %s", self.client_ip)
            raise DisconnectClientException()
        
        if hasattr(invoke['cmdData'], 'tcUrl'):
            self.tcUrl = invoke['cmdData'].tcUrl

        if hasattr(invoke['cmdData'], 'swfUrl'):
            self.swfUrl = invoke['cmdData'].swfUrl

        if hasattr(invoke['cmdData'], 'flashVer'):
            self.flashVer = invoke['cmdData'].flashVer

        if hasattr(invoke['cmdData'], 'objectEncoding'):
            self.objectEncoding = invoke['cmdData'].objectEncoding

        self.logger.info("App: %s, tcUrl: %s, swfUrl: %s, flashVer: %s", self.app, self.tcUrl, self.swfUrl, self.flashVer)
        
        await self.send_window_ack(5000000)
        await self.set_chunk_size(self.out_chunk_size)
        await self.set_peer_bandwidth(5000000, 2)
        await self.respond_connect(invoke['id'])

    async def send(self, data):
        # Perform asynchronous sending operation
        self.logger.info("Sending data: %s", data)
        self.writer.write(data)
        await self.writer.drain()


    async def send_window_ack(self, size):
        rtmp_buffer = bytes.fromhex("02000000000004050000000000000000")
        rtmp_buffer = bytearray(rtmp_buffer)
        rtmp_buffer[12:16] = size.to_bytes(4, byteorder='big')
        await self.send(rtmp_buffer)
        self.logger.info("Set ack to %s", size)

    async def set_peer_bandwidth(self, size, bandwidth_type):
        rtmp_buffer = bytes.fromhex("0200000000000506000000000000000000")
        rtmp_buffer = bytearray(rtmp_buffer)
        rtmp_buffer[12:16] = size.to_bytes(4, byteorder='big')
        rtmp_buffer[16] = bandwidth_type
        await self.send(rtmp_buffer)
        self.logger.info("Set bandwidth to %s", size)


    async def set_chunk_size(self, out_chunk_size):
        rtmp_buffer = bytearray.fromhex("02000000000004010000000000000000")
        struct.pack_into('>I', rtmp_buffer, 12, out_chunk_size)
        await self.send(bytes(rtmp_buffer))

        
    async def respond_connect(self, tid):
        response = common.Command()
        response.id, response.name, response.type = tid, '_result', common.Message.RPC

        arg = amf.Object(
            level='status',
            code='NetConnection.Connect.Success',
            description='Connection succeeded.',
            fmsVer='MasterStream/8,2',
            capabilities = 31,
            objectEncoding = self.objectEncoding)
        
        response.setArg(arg)
        message = response.toMessage()
        self.logger.info("Sending connect response!")
        await self.writeMessage(message)

    async def writeMessage(self, message):

        if message.streamId in self.lastWriteHeaders:
            header = self.lastWriteHeaders[message.streamId]
        else:
            if self.nextChannelId <= PROTOCOL_CHANNEL_ID:
                self.nextChannelId = PROTOCOL_CHANNEL_ID + 1
            header, self.nextChannelId = common.Header(
                self.nextChannelId), self.nextChannelId + 1
            self.lastWriteHeaders[message.streamId] = header
        if message.type < message.AUDIO:
            header = common.Header(PROTOCOL_CHANNEL_ID)
        
        # now figure out the header data bytes
        if header.streamId != message.streamId or header.time == 0 or message.time <= header.time:
            header.streamId, header.type, header.size, header.time, header.delta = message.streamId, message.type, message.size, message.time, message.time
            control = common.Header.FULL
        elif header.size != message.size or header.type != message.type:
            header.type, header.size, header.time, header.delta = message.type, message.size, message.time, message.time - header.time
            control = common.Header.MESSAGE
        else:
            header.time, header.delta = message.time, message.time - header.time
            control = common.Header.TIME
        
        hdr = common.Header(
            channel=header.channel,
            time=header.delta if control in (
                common.Header.MESSAGE,
                common.Header.TIME) else header.time,
            size=header.size,
            type=header.type,
            streamId=header.streamId)
        assert message.size == len(message.data)
        data = b''
        while len(message.data) > 0:
            data = data + hdr.toBytes(control)  # gather header bytes
            count = min(self.out_chunk_size, len(message.data))
            data = data + message.data[:count]
            message.data = message.data[count:]
            control = common.Header.SEPARATOR  # incomplete message continuation
        try:
            await self.send(data)
            self.logger.info("Message sent!")
        except:
            self.logger.info("Error on sending message!")

    async def send_rtmp_packet(self, packet_type, channel_id, data, chunk_size):
        '''Method to send an RTMP packet.'''
        packet_header = await self.create_packet_header(packet_type, channel_id, len(data), chunk_size)
        packet_chunks = await self.split_data_into_chunks(data, chunk_size)

        # Send the packet header
        await self.send(packet_header)

        # Send the packet chunks
        for chunk in packet_chunks:
            await self.send(chunk)

    async def create_packet_header(self, packet_type, channel_id, data_length, chunk_size):
        header_bytes = bytearray()

        # Basic Header
        fmt = 0  # Set fmt to 0 for chunk type 0 (full header)
        header_bytes.append((fmt << 6) | (channel_id & 0x3F))

        # Message Header
        if data_length < 64:
            header_bytes.append(data_length)
        elif data_length < 16384:
            header_bytes.append((data_length >> 8) | 0x40)
            header_bytes.append(data_length & 0xFF)
        elif data_length < 16777216:
            header_bytes.append((data_length >> 16) | 0x80)
            header_bytes.append((data_length >> 8) & 0xFF)
            header_bytes.append(data_length & 0xFF)
        else:
            header_bytes.append(0x00)
            header_bytes.append((data_length >> 16) & 0xFF)
            header_bytes.append((data_length >> 8) & 0xFF)
            header_bytes.append(data_length & 0xFF)

        # Extended Timestamp
        header_bytes.extend(b'\x00\x00\x00')

        return bytes(header_bytes)


    async def split_data_into_chunks(self, data, chunk_size):
        chunks = []
        num_chunks = (len(data) + chunk_size - 1) // chunk_size

        for i in range(num_chunks):
            chunk_start = i * chunk_size
            chunk_end = (i + 1) * chunk_size
            chunk_data = data[chunk_start:chunk_end]
            chunks.append(chunk_data)

        return chunks

    def parse_amf0_invoke_message(self, rtmp_packet):
        offset = 1 if rtmp_packet['header']['type'] == RTMP_TYPE_FLEX_MESSAGE else 0
        payload = rtmp_packet['payload'][offset:rtmp_packet['header']['length']]
        amfReader = amf.AMF0(payload)
        inst = {}
        inst['type'] = rtmp_packet['header']['type']
        inst['time'] = rtmp_packet['header']['timestamp']
        inst['packet'] = rtmp_packet
        inst['cmd'] = amfReader.read()  # first field is command name
        
        try:
            if rtmp_packet['header']['type'] == RTMP_TYPE_FLEX_MESSAGE or rtmp_packet['header']['type'] == RTMP_TYPE_INVOKE:
                inst['id'] = amfReader.read()  # second field *may* be message id
                inst['cmdData'] = amfReader.read()  # third is command data
                if(inst['cmdData'] != None):
                    self.logger.info("Command Data %s", vars(inst['cmdData']))
            else:
                inst['id'] = 0
            inst['args'] = []  # others are optional
            while True:
                inst['args'].append(amfReader.read())  # amfReader.read()
        except EOFError:
            pass

        self.logger.info("Command %s", inst)
        return inst

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
