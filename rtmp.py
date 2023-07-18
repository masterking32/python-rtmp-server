import asyncio
import logging
import amf
import av
import common
import struct
from typing import Optional
import time
import handshake
import uuid

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

# RTMP channel constants
RTMP_CHANNEL_PROTOCOL = 2
RTMP_CHANNEL_INVOKE = 3
RTMP_CHANNEL_AUDIO = 4
RTMP_CHANNEL_VIDEO = 5
RTMP_CHANNEL_DATA = 6

# Protocol channel ID
PROTOCOL_CHANNEL_ID = 2

MAX_CHUNK_SIZE = 10485760

# Constants for Packet Types
PacketTypeSequenceStart = 0  # Represents the start of a video/audio sequence
PacketTypeCodedFrames = 1  # Represents a video/audio frame
PacketTypeSequenceEnd = 2  # Represents the end of a video/audio sequence
PacketTypeCodedFramesX = 3  # Represents an extended video/audio frame
PacketTypeMetadata = 4  # Represents a packet with metadata
PacketTypeMPEG2TSSequenceStart = 5  # Represents the start of an MPEG2-TS video/audio sequence

# Constants for FourCC values
FourCC_AV1 = b'av01'  # AV1 video codec
FourCC_VP9 = b'vp09'  # VP9 video codec
FourCC_HEVC = b'hvc1'  # HEVC video codec

# Dictionary to store live users
LiveUsers = {}
# Dictionary to store player users
PlayerUsers = {}

# Custom exception for disconnecting clients
class DisconnectClientException(Exception):
    pass

# Class representing the state of a connected client
class ClientState:
    def __init__(self):
        self.id = str(uuid.uuid4())
        self.client_ip = '0.0.0.0'

        # RTMP properties
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
        self.streams = 0
        self._time0 = time.time()
        self.stream_mode = None
        
        self.streamPath = ''
        self.publishStreamId = 0
        self.publishStreamPath = ''
        self.CacheState = 0
        self.IncomingPackets = {}
        self.Players = {}

        # Meta Data
        self.metaData = None
        self.metaDataPayload = None
        self.audioSampleRate = 0
        self.audioChannels = 1
        self.videoWidth = 0
        self.videoHeight = 0
        self.videoFps = 0
        self.Bitrate = 0

        self.isFirstAudioReceived = False
        self.isReceiveVideo = False
        self.aacSequenceHeader = None
        self.avcSequenceHeader = None
        self.audioCodec = 0
        self.audioCodecName = ''
        self.audioProfileName = ''
        self.videoCodec = 0
        self.videoCodecName = ''
        self.videoProfileName = ''
        self.videoCount = 0
        self.videoLevel = 0

        self.inAckSize = 0
        self.inLastAck = 0

# RTMP server class
class RTMPServer:
    def __init__(self, host='0.0.0.0', port=1935):
        # Socket
        # Server socket properties
        self.host = host
        self.port = port
        self.client_states = {}
        
        self.logger = logging.getLogger('RTMPServer')
        self.logger.setLevel(LogLevel)

    async def handle_client(self, reader, writer):
        # Create a new client state for each connected client
        client_state = ClientState()
        self.client_states[client_state.id] = client_state
        self.client_states[client_state.id].clientID = client_state.id

        self.client_states[client_state.id].reader = reader
        self.client_states[client_state.id].writer = writer

        self.client_states[client_state.id].client_ip  = writer.get_extra_info('peername')
        self.logger.info("New client connected: %s", self.client_states[client_state.id].client_ip)

        # Perform RTMP handshake
        try:
            await asyncio.wait_for(self.perform_handshake(client_state.id), timeout=5)
        except asyncio.TimeoutError:
            self.logger.error("Handshake timeout. Closing connection: %s", self.client_states[client_state.id].client_ip)
            await self.disconnect(client_state.id)
            return

        # Process RTMP messages
        while True:
            try:
                await self.get_chunk_data(client_state.id)
                
            except asyncio.TimeoutError:
                self.logger.debug("Connection timeout. Closing connection: %s", self.client_states[client_state.id].client_ip)
                break
            
            except DisconnectClientException:
                self.logger.debug("Disconnecting client: %s", self.client_states[client_state.id].client_ip)
                break
            
            except ConnectionAbortedError as e:
                self.logger.debug("Connection aborted by client: %s", self.client_states[client_state.id].client_ip)
                break
        
            except Exception as e:
                self.logger.error("An error occurred: %s", str(e))
                break

        await self.disconnect(client_state.id)
        
    async def disconnect(self, client_id):
        # Close the client connection
        client_state = self.client_states[client_id]
        if client_state.stream_mode == 'live':
            # Finish Stream for players!
            print("NEED DISCONNECT Players!")

        client_ip = client_state.client_ip
        for app in LiveUsers:
            if LiveUsers[app]['client_id'] == client_id:
                del LiveUsers[app]
                break

        client_state['IncomingPackets'].clear()

        del self.client_states[client_id]
        try:
            client_state.writer.close()
            await client_state.writer.wait_closed()
            self.logger.info("Client disconnected: %s", client_ip)
        except Exception as e:
            # Handle the exception here, perform other tasks, or log the error.
            self.logger.error(f"Error occurred while disconnecting client: {e}")


    async def get_chunk_data(self, client_id):
        # Read a chunk of data from the client
        client_state = self.client_states[client_id]
        try:
            chunk_data = await client_state.reader.readexactly(1)
            if not chunk_data:
                raise DisconnectClientException()
            
            cid = chunk_data[0] & 0b00111111
            
            # Chunk Basic Header field may be 1, 2, or 3 bytes, depending on the chunk stream ID.
            if cid == 0: # ChunkBasicHeader: 2
                chunk_data += await client_state.reader.readexactly(1) # Need read 1 more packet
                cid = 64 + chunk_data[1] # Chunk stream IDs 64-319 can be encoded in the 2-byte form of the header
            elif cid == 1: #ChunkBasicHeader: 3
                chunk_data += await client_state.reader.readexactly(2) # Need read 2 more packets
                cid = (64 + chunk_data[1] + chunk_data[2]) << 8 # Chunk stream IDs 64-65599 can be encoded in the 3-byte version of this field

            chunk_full = bytearray(chunk_data)
            fmt = (chunk_data[0] & 0b11000000) >> 6

            if not cid in client_state.IncomingPackets:
                client_state.IncomingPackets[cid] = self.createPacket(cid, fmt)
            
            # I'm afraid I suffer from memory leaks. :D
            client_state.IncomingPackets[cid]['last_received_time'] = time.time()
            self.clearPayloadIfTimeout(client_id, 120)

            header_data = bytearray()
             # Get Message Timestamp for FMT 0, 1, 2
            if fmt <= RTMP_CHUNK_TYPE_2:
                timestamp_bytes = await client_state.reader.readexactly(3)
                header_data += timestamp_bytes
                client_state.IncomingPackets[cid]['timestamp'] = int.from_bytes(timestamp_bytes, byteorder='big')
                del timestamp_bytes

            # Get Message Length and Message Type for FMT 0, 1
            if fmt <= RTMP_CHUNK_TYPE_1:
                length_bytes = await client_state.reader.readexactly(3)
                header_data += length_bytes
                type_bytes = await client_state.reader.readexactly(1)
                header_data += type_bytes
                client_state.IncomingPackets[cid]['payload_length'] = int.from_bytes(length_bytes, byteorder='big')
                client_state.IncomingPackets[cid]['msg_type_id'] = int.from_bytes(type_bytes, byteorder='big')
                client_state.IncomingPackets[cid]['payload'] = bytearray()
                del length_bytes
                del type_bytes
            
            # Get Message Stream ID for FMT 0
            if fmt == RTMP_CHUNK_TYPE_0: 
                streamID_bytes = await client_state.reader.readexactly(4)
                header_data += streamID_bytes
                client_state.IncomingPackets[cid]['msg_stream_id'] = int.from_bytes(streamID_bytes, byteorder='big')
                del streamID_bytes
            
            chunk_full += header_data
            
            # Set Main Packet Headers and payload_length for FMT 0, 1
            if fmt <= RTMP_CHUNK_TYPE_1: 
                # client_state.IncomingPackets[cid]['basic_header'] = chunk_data
                # client_state.IncomingPackets[cid]['header'] = header_data
                payload_length = client_state.IncomingPackets[cid]['payload_length']

            # Calculate Payload Remaining length for FMT 2,3 
            if fmt > RTMP_CHUNK_TYPE_1:
                payload_length = client_state.IncomingPackets[cid]['payload_length'] - len(client_state.IncomingPackets[cid]['payload'])

            # Check message type id
            if RTMP_TYPE_METADATA < client_state.IncomingPackets[cid]['msg_type_id']:
                self.logger.error("Invalid Packet Type: %s", str(client_state.IncomingPackets[cid]['msg_type_id']))
                raise DisconnectClientException()
            
            # Messages with type=3 should never have ext timestamp field according to standard. However that's not always the case in real life
            if client_state.IncomingPackets[cid]['timestamp'] == 0xffffff:  # Max Value check (16777215), Need to read extended timestamp
                extended_timestamp_bytes = await client_state.reader.readexactly(4)
                chunk_full += extended_timestamp_bytes
                client_state.IncomingPackets[cid]['extended_timestamp'] = int.from_bytes(extended_timestamp_bytes, byteorder='big')
                del extended_timestamp_bytes

            client_state.inAckSize += len(chunk_full)

            self.logger.debug(f"FMT: {fmt}, CID: {cid}, Message Length: {payload_length}, Timestamp: {client_state.IncomingPackets[cid]['timestamp']}")

            if payload_length > 0:
                payload_length = min(client_state.chunk_size, payload_length)
                payload = await client_state.reader.readexactly(payload_length)
                client_state.inAckSize += len(payload)
                client_state.IncomingPackets[cid]['payload'] += payload
                del payload
            else:
                # I'm not sure. In some cases, I may need to disconnect the client, while in other cases, I won't. I will ignore the issue and proceed to the next packet, but I will clear the payload. If invalid data continues, it may result in a disconnection when processing subsequent packets.
                self.logger.error(f"Invalid Length (ZERO!), FMT: {fmt}, CID: {cid}, Message Length: {payload_length}, Timestamp: {client_state.IncomingPackets[cid]['timestamp']}")
                client_state.IncomingPackets[cid]['payload'] = bytearray()
                return
                
            if client_state.inAckSize >= 0xF0000000:
                client_state.inAckSize = 0
                client_state.inLastAck = 0
            
            # Delete some variables for fun!
            del chunk_data
            del chunk_full
            del payload_length
            del header_data

            if len(client_state.IncomingPackets[cid]['payload']) >= client_state.IncomingPackets[cid]['payload_length']:
                rtmp_packet = {
                    "header": {
                        "fmt": client_state.IncomingPackets[cid]["fmt"],
                        "cid": client_state.IncomingPackets[cid]["cid"],
                        "timestamp": client_state.IncomingPackets[cid]["timestamp"],
                        "length": client_state.IncomingPackets[cid]["payload_length"],
                        "type": client_state.IncomingPackets[cid]["msg_type_id"],
                        "stream_id": client_state.IncomingPackets[cid]["msg_stream_id"]
                    },
                    "clock": 0,
                    "payload": client_state.IncomingPackets[cid]['payload']
                }
                client_state.IncomingPackets[cid]['payload'] = bytearray()
                await self.handle_rtmp_packet(client_id, rtmp_packet)
                del rtmp_packet

            # Send ACK If needed!
            if(client_state.window_acknowledgement_size > 0 and client_state.inAckSize - client_state.inLastAck >= client_state.window_acknowledgement_size):
                client_state.inLastAck = client_state.inAckSize
                await self.send_ack(client_id, client_state.inAckSize)

        except Exception as e:
            self.logger.error("An error occurred: %s", str(e))
            raise DisconnectClientException()

    # This function is designed to safely stop memory leaks if they exist. It ensures that memory is properly managed and prevents any potential leaks from causing issues.
    def clearPayloadIfTimeout(self, client_id, packet_timeout=30):
        client_state = self.client_states[client_id]
        current_time = time.time()
        for cid, packet in client_state.IncomingPackets.items():
            if 'last_received_time' in packet and current_time - packet['last_received_time'] >= packet_timeout:
                packet['payload'] = bytearray()  # Clear the payload

    def createPacket(self, cid, fmt):
        out = {}
        out['fmt'] = fmt
        out['cid'] = cid
        # out['basic_header'] = bytearray()
        # out['header'] = bytearray()

        out['timestamp'] = 0
        out['extended_timestamp'] = 0
        out['payload_length'] = 0
        out['msg_type_id'] = 0
        out['msg_stream_id'] = 0
        out['payload'] = bytearray()
        out['last_received_time'] = time.time()

        return out

    async def perform_handshake(self, client_id):
        # Perform the RTMP handshake with the client
        client_state = self.client_states[client_id]
        
        c0_data = await client_state.reader.readexactly(1)
        if c0_data != bytes([0x03]) and c0_data != bytes([0x06]):
            client_state.writer.close()
            await client_state.writer.wait_closed()
            self.logger.info("Invalid Handshake, Client disconnected: %s", self.client_ip)

        c1_data = await client_state.reader.readexactly(1536)
        clientType = bytes([3])
        messageFormat = handshake.detectClientMessageFormat(c1_data)
        if messageFormat == handshake.MESSAGE_FORMAT_0:
            await self.send(client_id, clientType)
            s1_data = c1_data
            s2_data = c1_data
            await self.send(client_id, c1_data)
            await client_state.reader.readexactly(len(s1_data))
            await self.send(client_id, s2_data)
        else:
            s1_data = handshake.generateS1(messageFormat)
            s2_data = handshake.generateS2(messageFormat, c1_data)
            data = clientType + s1_data + s2_data
            client_state.writer.write(data)
            s1_data = await client_state.reader.readexactly(len(s1_data))

        self.logger.debug("Handshake done!")

    async def handle_rtmp_packet(self, client_id, rtmp_packet):
        # Handle an RTMP packet from the client
        # client_state = self.client_states[client_id]

        # Extract information from rtmp_packet and process as needed
        msg_type_id = rtmp_packet["header"]["type"]
        payload = rtmp_packet["payload"]
        # self.logger.debug("Received RTMP packet:")
        # self.logger.debug("  RTMP Packet Type: %s", msg_type_id)
    
        if msg_type_id == RTMP_TYPE_SET_CHUNK_SIZE:
            self.handle_chunk_size_message(client_id, payload)
        elif msg_type_id == RTMP_TYPE_ACKNOWLEDGEMENT:
            await self.handle_bytes_read_report(client_id, payload)
        # elif msg_type_id == RTMP_PACKET_TYPE_CONTROL:
        #     self.handle_control_message(payload)
        elif msg_type_id == RTMP_TYPE_WINDOW_ACKNOWLEDGEMENT_SIZE:
            self.handle_window_acknowledgement_size(client_id, payload)
        elif msg_type_id == RTMP_TYPE_SET_PEER_BANDWIDTH:
            self.handle_set_peer_bandwidth(client_id, payload)
        elif msg_type_id == RTMP_TYPE_AUDIO:
            await self.handle_audio_data(client_id, rtmp_packet)
        elif msg_type_id == RTMP_TYPE_VIDEO:
            await self.handle_video_data(client_id, rtmp_packet)
        # elif msg_type_id == RTMP_TYPE_FLEX_STREAM:
        #     self.handle_flex_stream_message(payload)
        # elif msg_type_id == RTMP_TYPE_FLEX_OBJECT:
        #     self.handle_flex_shared_object_message(payload)
        elif msg_type_id == RTMP_TYPE_FLEX_MESSAGE:
            invoke_message = self.parse_amf0_invoke_message(rtmp_packet)
            await self.handle_invoke_message(client_id, invoke_message)
        elif msg_type_id == RTMP_TYPE_DATA:
            await self.handle_amf_data(client_id, rtmp_packet)
        # elif msg_type_id == RTMP_TYPE_SHARED_OBJECT:
        #     self.handle_amf0_shared_object_message(payload)
        elif msg_type_id == RTMP_TYPE_INVOKE:
            invoke_message = self.parse_amf0_invoke_message(rtmp_packet)
            await self.handle_invoke_message(client_id, invoke_message)
        # elif msg_type_id == RTMP_TYPE_METADATA:
        #     self.handle_metadata_message(payload)
        else:
            self.logger.debug("Unsupported RTMP packet type: %s", msg_type_id)

    async def handle_video_data(self, client_id, rtmp_packet):
        # Handle video data in an RTMP packet
        client_state = self.client_states[client_id]
        payload = rtmp_packet['payload']
        isExHeader = (payload[0] >> 4 & 0b1000) != 0
        frame_type = payload[0] >> 4 & 0b0111
        codec_id = payload[0] & 0x0f
        packetType = payload[0] & 0x0f
        # Handle Video Data!
        if isExHeader:
            if packetType == PacketTypeMetadata:
                pass
            elif packetType == PacketTypeSequenceEnd:
                pass

            FourCC = payload[1:5]
            if FourCC == FourCC_HEVC:
                codec_id = 12
                if packetType == PacketTypeSequenceStart:
                    payload[0] = 0x1c
                    payload[1:5] = b'\x00\x00\x00\x00'
                elif packetType in [PacketTypeCodedFrames, PacketTypeCodedFramesX]:
                    if packetType == PacketTypeCodedFrames:
                        payload = payload[3:]
                    else:
                        payload[2:5] = b'\x00\x00\x00'
                    payload[0] = (frame_type << 4) | 0x0c
                    payload[1] = 1
            elif FourCC == FourCC_AV1:
                codec_id = 13
                if packetType == PacketTypeSequenceStart:
                    payload[0] = 0x1d
                    payload[1:5] = b'\x00\x00\x00\x00'
                elif packetType == PacketTypeMPEG2TSSequenceStart:
                    pass
                elif packetType == PacketTypeCodedFrames:
                    payload[0] = (frame_type << 4) | 0x0d
                    payload[1] = 1
                    payload[2:5] = b'\x00\x00\x00'
            else:
                self.logger.debug("unsupported extension header")
                return
            
        if codec_id in [7, 12, 13]:
            if frame_type == 1 and payload[1] == 0:
                client_state.avcSequenceHeader = bytearray(payload)
                info = av.readAVCSpecificConfig(client_state.avcSequenceHeader)
                client_state.videoWidth = info['width']
                client_state.videoHeight = info['height']
                client_state.videoProfileName = av.getAVCProfileName(info)
                client_state.videoLevel = info['level']
                self.logger.info("CodecID: %d, Video Level: %f, Profile Name: %s, Width: %d, Height: %d, Profile: %d",
                         codec_id, client_state.videoLevel, client_state.videoProfileName,
                         client_state.videoWidth, client_state.videoHeight, info['profile'])

        if client_state.videoCodec == 0:
            client_state.videoCodec = codec_id
            client_state.videoCodecName = common.VIDEO_CODEC_NAME[codec_id]
            self.logger.info("Codec Name: %s", client_state.videoCodecName)

        
    async def handle_audio_data(self, client_id, rtmp_packet):
        client_state = self.client_states[client_id]
        payload = rtmp_packet['payload']
        sound_format = (payload[0] >> 4) & 0x0f
        sound_type = payload[0] & 0x01
        sound_size = (payload[0] >> 1) & 0x01
        sound_rate = (payload[0] >> 2) & 0x03

        if client_state.audioCodec == 0:
            client_state.audioCodec = sound_format;
            client_state.audioCodecName = av.AUDIO_CODEC_NAME[sound_format];
            client_state.audioSampleRate = av.AUDIO_SOUND_RATE[sound_rate];
            client_state.audioChannels = sound_type + 1;
    
            if sound_format == 4:
                # Nellymoser 16 kHz
                client_state.audioSampleRate = 16000
            elif sound_format in (5, 7, 8):
                # Nellymoser 8 kHz | G.711 A-law | G.711 mu-law
                client_state.audioSampleRate = 8000
            elif sound_format == 11:
                # Speex
                client_state.audioSampleRate = 16000
            elif sound_format == 14:
                # MP3 8 kHz
                client_state.audioSampleRate = 8000

        if (sound_format == 10 or sound_format == 13) and payload[1] == 0:
            # cache AAC sequence header
            client_state.isFirstAudioReceived = True
            client_state.aacSequenceHeader = payload

            if sound_format == 10:
                info = av.read_aac_specific_config(client_state.aacSequenceHeader)
                client_state.audioProfileName = av.get_aac_profile_name(info)
                client_state.audioSampleRate = info['sample_rate']
                client_state.audioChannels = info['sample_rate']
            else:
                client_state.audioSampleRate = 48000
                client_state.audioChannels = payload[11]
        
        #write for players


    def handle_chunk_size_message(self, client_id, payload):
        # Handle Chunk Size message
        new_chunk_size = int.from_bytes(payload, byteorder='big')
        if(MAX_CHUNK_SIZE < new_chunk_size):
            self.logger.debug("Chunk size is too big!", new_chunk_size)
            raise DisconnectClientException()
        
        self.client_states[client_id].chunk_size = new_chunk_size
        self.logger.debug("Updated chunk size: %d", self.client_states[client_id].chunk_size)

    def handle_window_acknowledgement_size(self, client_id, payload):
        # Handle Window Acknowledgement Size message
        client_state = self.client_states[client_id]
        new_window_acknowledgement_size = int.from_bytes(payload, byteorder='big')
        client_state.window_acknowledgement_size = new_window_acknowledgement_size
        self.logger.debug("Updated window acknowledgement size: %d", client_state.window_acknowledgement_size)

    def handle_set_peer_bandwidth(self, client_id, payload):
        # Handle Set Peer Bandwidth message
        client_state = self.client_states[client_id]
        bandwidth = int.from_bytes(payload[:4], byteorder='big')
        limit_type = payload[4]
        client_state.peer_bandwidth = bandwidth
        self.logger.debug("Updated peer bandwidth: %d, Limit type: %d", client_state.peer_bandwidth, limit_type)

    async def handle_invoke_message(self, client_id, invoke):
        if invoke['cmd'] == 'connect':
            self.logger.debug("Received connect invoke")
            await self.handle_connect_command(client_id, invoke)
        elif invoke['cmd'] == 'releaseStream' or invoke['cmd'] == 'FCPublish'or invoke['cmd'] == 'FCUnpublish' or invoke['cmd'] == 'getStreamLength':
            self.logger.debug("Received %s invoke", invoke['cmd'])
            return
        elif invoke['cmd'] == 'createStream':
            self.logger.debug("Received createStream invoke")
            await self.response_createStream(client_id, invoke)
        elif invoke['cmd'] == 'publish':
            self.logger.debug("Received publish invoke")
            await self.handle_publish(client_id, invoke)
        elif invoke['cmd'] == 'play':
            self.logger.debug("Received play invoke")
            await self.handle_onPlay(client_id, invoke)
        # Need to add and support other CMDs.
        else:
            self.logger.info("Unsupported invoke command %s!", invoke['cmd'])
    
    async def handle_onPlay(self, client_id, invoke):
        client_state = self.client_states[client_id]
        if not client_state.app in LiveUsers:
            self.logger.warning("Stream not exists to play!")
            await self.sendStatusMessage(client_id, client_state.publishStreamId, "error", "NetStream.Play.BadName", "Stream not exists")
            raise DisconnectClientException()
        
        publisher_id = LiveUsers[client_state.app]['client_id']
        publisher_client_state = self.client_states[publisher_id]
        if publisher_client_state.metaDataPayload != None:
            # Sending Publisher Meta Data to Player!
            output = amf.AMFBytesIO()
            amfWriter = amf.AMF0(output)
            amfWriter.write('onMetaData')
            amfWriter.write(publisher_client_state.metaData)
            output.seek(0)
            payload = output.read()
            streamId = invoke['packet']['header']['stream_id']
            packet_header = common.Header(RTMP_CHANNEL_DATA, 0, len(payload), RTMP_TYPE_DATA, streamId)
            response = common.Message(packet_header, payload)
            await self.writeMessage(client_id, response)

    async def handle_publish(self, client_id, invoke):
        client_state = self.client_states[client_id]
        client_state.stream_mode = 'live' if len(invoke['args']) < 2 else invoke['args'][1]  # live, record, append
        client_state.streamPath = invoke['args'][0]
        client_state.publishStreamId = int(invoke['packet']['header']['stream_id'])
        client_state.publishStreamPath = "/" + client_state.app + "/" + client_state.streamPath.split("?")[0]
        if(client_state.streamPath == None or client_state.streamPath == ''):
            self.logger.warning("Stream key is empty!")
            await self.sendStatusMessage(client_id, client_state.publishStreamId, "error", "NetStream.publish.Unauthorized", "Authorization required.")
            raise DisconnectClientException()
        
        if client_state.stream_mode == 'live':
            if LiveUsers.get(client_state.app) is not None:
                self.logger.warning("Stream already publishing!")
                await self.sendStatusMessage(client_id, client_state.publishStreamId, "error", "NetStream.Publish.BadName", "Stream already publishing")
                raise DisconnectClientException()
        
            LiveUsers[client_state.app] = {
                'client_id': client_id,
                'stream_mode': client_state.stream_mode,
                'stream_path': client_state.streamPath,
                'publish_stream_id': client_state.publishStreamId,
                'app': client_state.app,
            }

        self.logger.info("Publish Request Mode: %s, App: %s, Path: %s, publishStreamPath: %s, StreamID: %s", client_state.stream_mode, client_state.app, client_state.streamPath, client_state.publishStreamPath, str(client_state.publishStreamId))
        await self.sendStatusMessage(client_id, client_state.publishStreamId, "status", "NetStream.Publish.Start", f"{client_state.publishStreamPath} is now published.")

    async def sendStatusMessage(self, client_id, sid, level, code, description):
        response = common.Command(
        name='onStatus',
        id=sid,
        tm=self.relativeTime(client_id),
        args=[
            amf.Object(
                level=level,
                code=code,
                description=description,
                details=None)])
        
        message = response.toMessage()
        self.logger.debug("Sending onStatus response!")
        await self.writeMessage(client_id, message)
        
    async def response_createStream(self, client_id, invoke):
        client_state = self.client_states[client_id]
        client_state.streams = client_state.streams + 1;
        response = common.Command(
            name='_result',
            id=invoke['id'],
            tm=self.relativeTime(client_id),
            type=common.Message.RPC,
            args=[client_state.streams])

        message = response.toMessage()
        self.logger.debug("Sending createStream response!")
        await self.writeMessage(client_id, message)

    async def handle_connect_command(self, client_id, invoke):
        client_state = self.client_states[client_id]
        if hasattr(invoke['cmdData'], 'app'):
            client_state.app = invoke['cmdData'].app

        if client_state.app == '':
            self.logger.warning("Empty 'app' attribute. Disconnecting client: %s", client_state.client_ip)
            raise DisconnectClientException()
        
        if hasattr(invoke['cmdData'], 'tcUrl'):
            client_state.tcUrl = invoke['cmdData'].tcUrl

        if hasattr(invoke['cmdData'], 'swfUrl'):
            client_state.swfUrl = invoke['cmdData'].swfUrl

        if hasattr(invoke['cmdData'], 'flashVer'):
            client_state.flashVer = invoke['cmdData'].flashVer

        if hasattr(invoke['cmdData'], 'objectEncoding'):
            client_state.objectEncoding = invoke['cmdData'].objectEncoding

        self.logger.info("App: %s, tcUrl: %s, swfUrl: %s, flashVer: %s", client_state.app, client_state.tcUrl, client_state.swfUrl, client_state.flashVer)
        
        await self.send_window_ack(client_id, 5000000)
        await self.set_chunk_size(client_id, client_state.out_chunk_size)
        await self.set_peer_bandwidth(client_id, 5000000, 2)
        await self.respond_connect(client_id, invoke['id'])

    async def send(self, client_id, data):
        client_state = self.client_states[client_id]
        # Perform asynchronous sending operation
        # self.logger.info("Sending data: %s", data)
        client_state.writer.write(data)
        await client_state.writer.drain()


    async def send_window_ack(self, client_id, size):
        rtmp_buffer = bytes.fromhex("02000000000004050000000000000000")
        rtmp_buffer = bytearray(rtmp_buffer)
        rtmp_buffer[12:16] = size.to_bytes(4, byteorder='big')
        await self.send(client_id, rtmp_buffer)
        self.logger.debug("Set ack to %s", size)

    async def send_ack(self, client_id, size):
        rtmp_buffer = bytes.fromhex("02000000000004030000000000000000")
        rtmp_buffer = bytearray(rtmp_buffer)
        rtmp_buffer[12:16] = size.to_bytes(4, byteorder='big')
        await self.send(client_id, rtmp_buffer)
        self.logger.debug("Send ACK: %s", size)

    async def set_peer_bandwidth(self, client_id, size, bandwidth_type):
        rtmp_buffer = bytes.fromhex("0200000000000506000000000000000000")
        rtmp_buffer = bytearray(rtmp_buffer)
        rtmp_buffer[12:16] = size.to_bytes(4, byteorder='big')
        rtmp_buffer[16] = bandwidth_type
        await self.send(client_id, rtmp_buffer)
        self.logger.debug("Set bandwidth to %s", size)

    async def set_chunk_size(self, client_id, out_chunk_size):
        rtmp_buffer = bytearray.fromhex("02000000000004010000000000000000")
        struct.pack_into('>I', rtmp_buffer, 12, out_chunk_size)
        await self.send(client_id, bytes(rtmp_buffer))
        self.logger.debug("Set out chunk to %s", out_chunk_size)

    async def handle_bytes_read_report(self, client_id, payload):
        # bytes_read = int.from_bytes(payload, byteorder='big')
        # self.logger.debug("Bytes read: %d", bytes_read)
        # # send ACK
        # rtmpBuffer = bytearray.fromhex('02000000000004030000000000000000')
        # rtmpBuffer[12:16] = bytes_read.to_bytes(4, 'big')
        # await self.send(client_id, rtmpBuffer) 
        # Just Ignore!
        return False
        
    async def respond_connect(self, client_id, tid):
        client_state = self.client_states[client_id]
        response = common.Command()
        response.id, response.name, response.type = tid, '_result', common.Message.RPC

        arg = amf.Object(
            level='status',
            code='NetConnection.Connect.Success',
            description='Connection succeeded.',
            fmsVer='MasterStream/8,2',
            capabilities = 31,
            objectEncoding = client_state.objectEncoding)
        
        response.setArg(arg)
        message = response.toMessage()
        self.logger.debug("Sending connect response!")
        await self.writeMessage(client_id, message)

    async def writeMessage(self, client_id, message):
        client_state = self.client_states[client_id]
        if message.streamId in client_state.lastWriteHeaders:
            header = client_state.lastWriteHeaders[message.streamId]
        else:
            if client_state.nextChannelId <= PROTOCOL_CHANNEL_ID:
                client_state.nextChannelId = PROTOCOL_CHANNEL_ID + 1
            header, client_state.nextChannelId = common.Header(
                client_state.nextChannelId), client_state.nextChannelId + 1
            client_state.lastWriteHeaders[message.streamId] = header
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
            count = min(client_state.out_chunk_size, len(message.data))
            data = data + message.data[:count]
            message.data = message.data[count:]
            control = common.Header.SEPARATOR  # incomplete message continuation
        try:
            await self.send(client_id, data)
            self.logger.debug("Message sent!")
        except:
            self.logger.debug("Error on sending message!")

    async def handle_amf_data(self, client_id, rtmp_packet):
        client_state = self.client_states[client_id]
        offset = 1 if rtmp_packet['header']['type'] == RTMP_TYPE_FLEX_MESSAGE else 0
        payload = rtmp_packet['payload'][offset:rtmp_packet['header']['length']]
        amfReader = amf.AMF0(payload)
        inst = {}
        inst['type'] = rtmp_packet['header']['type']
        inst['time'] = rtmp_packet['header']['timestamp']
        inst['packet'] = rtmp_packet
        inst['cmd'] = amfReader.read()  # first field is command name
        if inst['cmd'] == '@setDataFrame':
            inst['type'] = amfReader.read() # onMetaData
            self.logger.debug("AMF Data type: %s", inst['type'])
            if inst['type'] != 'onMetaData':
                return
            
            inst['dataObj'] = amfReader.read()  # third is obj data
            if(inst['dataObj'] != None):
                    self.logger.debug("Command Data %s", inst['dataObj'])
        else:
            self.logger.warning("Unsupported RTMP_TYPE_DATA cmd, CMD: %s", inst['cmd'])
        
        client_state.metaDataPayload = payload
        client_state.metaData = inst['dataObj']
        client_state.audioSampleRate = int(inst['dataObj']['audiosamplerate']);
        client_state.audioChannels = 2 if inst['dataObj']['stereo'] else 1
        client_state.videoWidth = int(inst['dataObj']['width']);
        client_state.videoHeight = int(inst['dataObj']['height']);
        client_state.videoFps = int(inst['dataObj']['framerate']);
        client_state.Bitrate = int(inst['dataObj']['videodatarate']);
        #TODO: handle Meta Data!

    def parse_amf0_invoke_message(self, rtmp_packet):
        offset = 1 if rtmp_packet['header']['type'] == RTMP_TYPE_FLEX_MESSAGE else 0
        payload = rtmp_packet['payload'][offset:rtmp_packet['header']['length']]
        amfReader = amf.AMF0(payload)
        inst = {}
        inst['type'] = rtmp_packet['header']['type']
        inst['time'] = rtmp_packet['header']['timestamp']
        inst['packet'] = rtmp_packet
        
        try:
            inst['cmd'] = amfReader.read()  # first field is command name
            if rtmp_packet['header']['type'] == RTMP_TYPE_FLEX_MESSAGE or rtmp_packet['header']['type'] == RTMP_TYPE_INVOKE:
                inst['id'] = amfReader.read()  # second field *may* be message id
                inst['cmdData'] = amfReader.read()  # third is command data
                if(inst['cmdData'] != None):
                    self.logger.debug("Command Data %s", vars(inst['cmdData']))
            else:
                inst['id'] = 0
            inst['args'] = []  # others are optional
            while True:
                inst['args'].append(amfReader.read())  # amfReader.read()
        except EOFError:
            pass

        self.logger.debug("Command %s", inst)
        return inst
    
    def relativeTime(self, client_id):
        return int(1000 * (time.time() - self.client_states[client_id]._time0))

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
