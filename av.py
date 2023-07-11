
AAC_SAMPLE_RATE = [
  96000, 88200, 64000, 48000,
  44100, 32000, 24000, 22050,
  16000, 12000, 11025, 8000,
  7350, 0, 0, 0
]

AAC_CHANNELS = [
  0, 1, 2, 3, 4, 5, 6, 8
]

AUDIO_CODEC_NAME = [
  '',
  'ADPCM',
  'MP3',
  'LinearLE',
  'Nellymoser16',
  'Nellymoser8',
  'Nellymoser',
  'G711A',
  'G711U',
  '',
  'AAC',
  'Speex',
  '',
  'OPUS',
  'MP3-8K',
  'DeviceSpecific',
  'Uncompressed'
]

AUDIO_SOUND_RATE = [
  5512, 11025, 22050, 44100
]

VIDEO_CODEC_NAME = [
  '',
  'Jpeg',
  'Sorenson-H263',
  'ScreenVideo',
  'On2-VP6',
  'On2-VP6-Alpha',
  'ScreenVideo2',
  'H264',
  '',
  '',
  '',
  '',
  'H265',
  'AV1'
]

class Bitop:
    def __init__(self, buffer):
        self.buffer = buffer
        self.buflen = len(buffer)
        self.bufpos = 0
        self.bufoff = 0
        self.iserro = False
    
    def read(self, n):
        v = 0
        d = 0
        while n:
            if n < 0 or self.bufpos >= self.buflen:
                self.iserro = True
                return 0
            
            self.iserro = False
            d = self.bufoff + n > 8 and 8 - self.bufoff or n
            
            v <<= d
            v += (self.buffer[self.bufpos] >> (8 - self.bufoff - d)) & (0xff >> (8 - d))
            
            self.bufoff += d
            n -= d
            
            if self.bufoff == 8:
                self.bufpos += 1
                self.bufoff = 0
        
        return v
    
    def look(self, n):
        p = self.bufpos
        o = self.bufoff
        v = self.read(n)
        self.bufpos = p
        self.bufoff = o
        return v
    
    def read_golomb(self):
        n = 0
        while self.read(1) == 0 and not self.iserro:
            n += 1
        return (1 << n) + self.read(n) - 1

def get_object_type(bitop):
    audio_object_type = bitop.read(5)
    if audio_object_type == 31:
        audio_object_type = bitop.read(6) + 32
    return audio_object_type


def get_sample_rate(bitop, info):
    info['sampling_index'] = bitop.read(4)
    return info['sampling_index'] == 0x0f and bitop.read(24) or AAC_SAMPLE_RATE[info['sampling_index']]


def read_aac_specific_config(aac_sequence_header):
    info = {}
    bitop = Bitop(aac_sequence_header)
    bitop.read(16)
    info["object_type"] = get_object_type(bitop)
    info["sample_rate"] = get_sample_rate(bitop, info)
    info["chan_config"] = bitop.read(4)
    if info["chan_config"] < len(AAC_CHANNELS):
        info["channels"] = AAC_CHANNELS[info["chan_config"]]
    info["sbr"] = -1
    info["ps"] = -1
    if info["object_type"] == 5 or info["object_type"] == 29:
        if info["object_type"] == 29:
            info["ps"] = 1
        info["ext_object_type"] = 5
        info["sbr"] = 1
        info["sample_rate"] = get_sample_rate(bitop, info)
        info["object_type"] = get_object_type(bitop)

    return info


def get_aac_profile_name(info):
    if info['object_type'] == 1:
        return 'Main'
    elif info['object_type'] == 2:
        return 'HEv2' if info['ps'] > 0 else 'HE' if info['sbr'] > 0 else 'LC'
    elif info['object_type'] == 3:
        return 'SSR'
    elif info['object_type'] == 4:
        return 'LTP'
    elif info['object_type'] == 5:
        return 'SBR'
    else:
        return ''

def read_h264_specific_config(avc_sequence_header):
    info = {}  # Define info as a dictionary
    bitop = Bitop(avc_sequence_header)
    bitop.read(48)
    info['width'] = 0  
    info['height'] = 0  

    while True:
        info['profile'] = bitop.read(8)  
        info['compat'] = bitop.read(8)  
        info['level'] = bitop.read(8)  
        info['nalu'] = (bitop.read(8) & 0x03) + 1  
        info['nb_sps'] = bitop.read(8) & 0x1F  
        if info['nb_sps'] == 0:
            break

        bitop.read(16)

        if bitop.read(8) != 0x67:
            break

        profile_idc = bitop.read(8)
        bitop.read(8)
        bitop.read(8)
        bitop.read_golomb()

        if profile_idc in [100, 110, 122, 244, 44, 83, 86, 118]:
            cf_idc = bitop.read_golomb()

            if cf_idc == 3:
                bitop.read(1)

            bitop.read_golomb()
            bitop.read_golomb()
            bitop.read(1)

            if bitop.read(1):
                for n in range(8 if cf_idc != 3 else 12):
                    if bitop.read(1):
                        pass  # TODO: scaling_list()

        bitop.read_golomb()

        case = bitop.read_golomb()
        if case == 0:
            bitop.read_golomb()
        elif case == 1:
            bitop.read(1)
            bitop.read_golomb()
            bitop.read_golomb()
            bitop.read_golomb()
            num_ref_frames = bitop.read_golomb()
            for n in range(num_ref_frames):
                bitop.read_golomb()

        info['avc_ref_frames'] = bitop.read_golomb()  
        bitop.read(1)
        width = bitop.read_golomb()
        height = bitop.read_golomb()
        frame_mbs_only = bitop.read(1)

        if not frame_mbs_only:
            bitop.read(1)

        bitop.read(1)

        if bitop.read(1):
            crop_left = bitop.read_golomb()
            crop_right = bitop.read_golomb()
            crop_top = bitop.read_golomb()
            crop_bottom = bitop.read_golomb()
        else:
            crop_left = 0
            crop_right = 0
            crop_top = 0
            crop_bottom = 0

        info['level'] = info['level'] / 10.0  
        info['width'] = (width + 1) * 16 - (crop_left + crop_right) * 2  
        info['height'] = (2 - frame_mbs_only) * (height + 1) * 16 - (crop_top + crop_bottom) * 2  

    return info

def hevc_parse_ptl(bitop, hevc, max_sub_layers_minus1):
    general_ptl = {}  # Define general_ptl as a dictionary

    general_ptl['profile_space'] = bitop.read(2)  
    general_ptl['tier_flag'] = bitop.read(1)  
    general_ptl['profile_idc'] = bitop.read(5)  
    general_ptl['profile_compatibility_flags'] = bitop.read(32)  
    general_ptl['general_progressive_source_flag'] = bitop.read(1)  
    general_ptl['general_interlaced_source_flag'] = bitop.read(1)  
    general_ptl['general_non_packed_constraint_flag'] = bitop.read(1)  
    general_ptl['general_frame_only_constraint_flag'] = bitop.read(1)  
    bitop.read(32)
    bitop.read(12)
    general_ptl['level_idc'] = bitop.read(8)  

    general_ptl['sub_layer_profile_present_flag'] = []
    general_ptl['sub_layer_level_present_flag'] = []

    for i in range(max_sub_layers_minus1):
        general_ptl['sub_layer_profile_present_flag'].append(bitop.read(1))  
        general_ptl['sub_layer_level_present_flag'].append(bitop.read(1))  

    if max_sub_layers_minus1 > 0:
        for i in range(max_sub_layers_minus1, 8):
            bitop.read(2)

    general_ptl['sub_layer_profile_space'] = []
    general_ptl['sub_layer_tier_flag'] = []
    general_ptl['sub_layer_profile_idc'] = []
    general_ptl['sub_layer_profile_compatibility_flag'] = []
    general_ptl['sub_layer_progressive_source_flag'] = []
    general_ptl['sub_layer_interlaced_source_flag'] = []
    general_ptl['sub_layer_non_packed_constraint_flag'] = []
    general_ptl['sub_layer_frame_only_constraint_flag'] = []
    general_ptl['sub_layer_level_idc'] = []

    for i in range(max_sub_layers_minus1):
        if general_ptl['sub_layer_profile_present_flag'][i]:
            general_ptl['sub_layer_profile_space'].append(bitop.read(2))  
            general_ptl['sub_layer_tier_flag'].append(bitop.read(1))  
            general_ptl['sub_layer_profile_idc'].append(bitop.read(5))  
            general_ptl['sub_layer_profile_compatibility_flag'].append(bitop.read(32))  
            general_ptl['sub_layer_progressive_source_flag'].append(bitop.read(1))  
            general_ptl['sub_layer_interlaced_source_flag'].append(bitop.read(1))  
            general_ptl['sub_layer_non_packed_constraint_flag'].append(bitop.read(1))  
            general_ptl['sub_layer_frame_only_constraint_flag'].append(bitop.read(1))  
            bitop.read(32)
            bitop.read(12)
        if general_ptl['sub_layer_level_present_flag'][i]:
            general_ptl['sub_layer_level_idc'].append(bitop.read(8))  
        else:
            general_ptl['sub_layer_level_idc'].append(1)

    return general_ptl

def hevc_parse_sps(sps, hevc):
    psps = {}
    num_bytes_in_nal_unit = len(sps)
    num_bytes_in_rbsp = 0
    rbsp_array = []
    bitop = Bitop(sps)

    bitop.read(1)
    bitop.read(6)
    bitop.read(6)
    bitop.read(3)

    for i in range(2, num_bytes_in_nal_unit):
        if i + 2 < num_bytes_in_nal_unit and bitop.look(24) == 0x000003:
            rbsp_array.append(bitop.read(8))
            rbsp_array.append(bitop.read(8))
            i += 2
            bitop.read(8)
        else:
            rbsp_array.append(bitop.read(8))

    rbsp = bytes(rbsp_array)
    rbsp_bitop = Bitop(rbsp)
    psps['sps_video_parameter_set_id'] = rbsp_bitop.read(4)
    psps['sps_max_sub_layers_minus1'] = rbsp_bitop.read(3)
    psps['sps_temporal_id_nesting_flag'] = rbsp_bitop.read(1)
    psps['profile_tier_level'] = hevc_parse_ptl(rbsp_bitop, hevc, psps['sps_max_sub_layers_minus1'])
    psps['sps_seq_parameter_set_id'] = rbsp_bitop.read_golomb()
    psps['chroma_format_idc'] = rbsp_bitop.read_golomb()
    if psps['chroma_format_idc'] == 3:
        psps['separate_colour_plane_flag'] = rbsp_bitop.read(1)
    else:
        psps['separate_colour_plane_flag'] = 0
    psps['pic_width_in_luma_samples'] = rbsp_bitop.read_golomb()
    psps['pic_height_in_luma_samples'] = rbsp_bitop.read_golomb()
    psps['conformance_window_flag'] = rbsp_bitop.read(1)
    psps['conf_win_left_offset'] = 0
    psps['conf_win_right_offset'] = 0
    psps['conf_win_top_offset'] = 0
    psps['conf_win_bottom_offset'] = 0
    if psps['conformance_window_flag']:
        vert_mult = 1 + (psps['chroma_format_idc'] < 2)
        horiz_mult = 1 + (psps['chroma_format_idc'] < 3)
        psps['conf_win_left_offset'] = rbsp_bitop.read_golomb() * horiz_mult
        psps['conf_win_right_offset'] = rbsp_bitop.read_golomb() * horiz_mult
        psps['conf_win_top_offset'] = rbsp_bitop.read_golomb() * vert_mult
        psps['conf_win_bottom_offset'] = rbsp_bitop.read_golomb() * vert_mult
    
    return psps


def read_hevc_specific_config(hevc_sequence_header):
    info = {}
    info["width"] = 0
    info["height"] = 0
    info["profile"] = 0
    info["level"] = 0
    hevc_sequence_header = hevc_sequence_header[5:]

    while True:
        hevc = {}
        if len(hevc_sequence_header) < 23:
            break

        hevc["configurationVersion"] = hevc_sequence_header[0]
        if hevc["configurationVersion"] != 1:
            break

        hevc["general_profile_space"] = (hevc_sequence_header[1] >> 6) & 0x03
        hevc["general_tier_flag"] = (hevc_sequence_header[1] >> 5) & 0x01
        hevc["general_profile_idc"] = hevc_sequence_header[1] & 0x1F
        hevc["general_profile_compatibility_flags"] = (hevc_sequence_header[2] << 24) | (hevc_sequence_header[3] << 16) | (hevc_sequence_header[4] << 8) | hevc_sequence_header[5]
        hevc["general_constraint_indicator_flags"] = ((hevc_sequence_header[6] << 24) | (hevc_sequence_header[7] << 16) | (hevc_sequence_header[8] << 8) | hevc_sequence_header[9])
        hevc["general_constraint_indicator_flags"] = (hevc["general_constraint_indicator_flags"] << 16) | (hevc_sequence_header[10] << 8) | hevc_sequence_header[11]
        hevc["general_level_idc"] = hevc_sequence_header[12]
        hevc["min_spatial_segmentation_idc"] = ((hevc_sequence_header[13] & 0x0F) << 8) | hevc_sequence_header[14]
        hevc["parallelismType"] = hevc_sequence_header[15] & 0x03
        hevc["chromaFormat"] = hevc_sequence_header[16] & 0x03
        hevc["bitDepthLumaMinus8"] = hevc_sequence_header[17] & 0x07
        hevc["bitDepthChromaMinus8"] = hevc_sequence_header[18] & 0x07
        hevc["avgFrameRate"] = (hevc_sequence_header[19] << 8) | hevc_sequence_header[20]
        hevc["constantFrameRate"] = (hevc_sequence_header[21] >> 6) & 0x03
        hevc["numTemporalLayers"] = (hevc_sequence_header[21] >> 3) & 0x07
        hevc["temporalIdNested"] = (hevc_sequence_header[21] >> 2) & 0x01
        hevc["lengthSizeMinusOne"] = hevc_sequence_header[21] & 0x03

        hevc["numOfArrays"] = hevc_sequence_header[22]
        hevc_sequence_header = hevc_sequence_header[23:]

        for i in range(hevc["numOfArrays"]):
            if len(hevc_sequence_header) < 3:
                break
            hevc["array_completeness"] = (hevc_sequence_header[0] >> 7) & 0x01
            hevc["array_nal_type"] = hevc_sequence_header[0] & 0x3F
            hevc["numNalus"] = (hevc_sequence_header[1] << 8) | hevc_sequence_header[2]
            hevc_sequence_header = hevc_sequence_header[3:]

            for j in range(hevc["numNalus"]):
                if len(hevc_sequence_header) < 2:
                    break
                hevc["nal_length"] = (hevc_sequence_header[0] << 8) | hevc_sequence_header[1]
                hevc_sequence_header = hevc_sequence_header[2:]

                if len(hevc_sequence_header) < hevc["nal_length"]:
                    break
                hevc["nal_unit"] = hevc_sequence_header[:hevc["nal_length"]]
                hevc_sequence_header = hevc_sequence_header[hevc["nal_length"]:]

                if hevc["array_nal_type"] == 33:
                    info = hevc_parse_sps(hevc["nal_unit"][4:], hevc)

        break

    if info["width"] != 0 and info["height"] != 0:
        info["profile_name"] = 'HEVC-' + str(info["profile"])
    else:
        info["profile_name"] = ''

    return info

def parse_flv_header(header):
    if len(header) < 13:
        return {}

    flv_header = {}
    flv_header["signature"] = header[:3]
    flv_header["version"] = header[3]
    flv_header["flags"] = header[4]
    flv_header["offset"] = (header[5] << 24) | (header[6] << 16) | (header[7] << 8) | header[8]
    flv_header["previousTagSize"] = (header[9] << 24) | (header[10] << 16) | (header[11] << 8) | header[12]

    return flv_header

def parse_tag_header(header):
    if len(header) < 11:
        return {}

    tag_header = {}
    tag_header['type'] = header[0]
    tag_header['dataSize'] = (header[1] << 16) | (header[2] << 8) | header[3]
    tag_header['timestamp'] = (header[4] << 16) | (header[5] << 8) | header[6]
    tag_header['timestampExtended'] = header[7]
    tag_header['streamID'] = (header[8] << 16) | (header[9] << 8) | header[10]

    return tag_header

def parse_flv_body(data):
    tags = []
    offset = 0

    while offset < len(data):
        tag_header = parse_tag_header(data[offset:])
        offset += 11

        if not tag_header:
            break

        tag_data = data[offset:offset + tag_header['dataSize']]
        offset += tag_header['dataSize']

        if tag_header['type'] == 8:
            audio_info = {}
            audio_info['soundFormat'] = (tag_data[0] >> 4) & 0x0F
            audio_info['soundRate'] = (tag_data[0] >> 2) & 0x03
            audio_info['soundSize'] = (tag_data[0] >> 1) & 0x01
            audio_info['soundType'] = tag_data[0] & 0x01
            audio_info['aacPacketType'] = tag_data[1]

            if audio_info['soundFormat'] == 10 and audio_info['aacPacketType'] == 0:
                audio_info['aacSequenceHeader'] = tag_data[2:]
                audio_info['aacSpecificConfig'] = read_aac_specific_config(audio_info['aacSequenceHeader'])
                audio_info['codecName'] = AUDIO_CODEC_NAME[10]
                audio_info['profile'] = get_aac_profile_name(audio_info['aacSpecificConfig'])
                audio_info['sampleRate'] = audio_info['aacSpecificConfig']['sample_rate']
                audio_info['channels'] = audio_info['aacSpecificConfig']['channels']

            tags.append(audio_info)
        elif tag_header['type'] == 9:
            video_info = {}
            video_info['frameType'] = (tag_data[0] >> 4) & 0x0F
            video_info['codecID'] = tag_data[0] & 0x0F

            if video_info['codecID'] == 7:
                video_info['avcPacketType'] = tag_data[1]
                if video_info['avcPacketType'] == 0:
                    video_info['avcSequenceHeader'] = tag_data[2:]
                    video_info['avcSpecificConfig'] = read_h264_specific_config(video_info['avcSequenceHeader'])
                    video_info['codecName'] = VIDEO_CODEC_NAME[7]
                    video_info['profile'] = video_info['avcSpecificConfig']['profile']
                    video_info['level'] = video_info['avcSpecificConfig']['level']
                    video_info['width'] = video_info['avcSpecificConfig']['width']
                    video_info['height'] = video_info['avcSpecificConfig']['height']

            elif video_info['codecID'] == 12:
                video_info['avcPacketType'] = tag_data[1]
                if video_info['avcPacketType'] == 0:
                    video_info['hevcSequenceHeader'] = tag_data[2:]
                    video_info['hevcSpecificConfig'] = read_hevc_specific_config(video_info['hevcSequenceHeader'])
                    video_info['codecName'] = VIDEO_CODEC_NAME[12]
                    video_info['profile'] = video_info['hevcSpecificConfig']['profile_name']
                    video_info['level'] = video_info['hevcSpecificConfig']['level']
                    video_info['width'] = video_info['hevcSpecificConfig']['width']
                    video_info['height'] = video_info['hevcSpecificConfig']['height']

            tags.append(video_info)

    return tags
