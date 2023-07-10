
import random
import hashlib
import hmac

MESSAGE_FORMAT_0 = 0
MESSAGE_FORMAT_1 = 1
MESSAGE_FORMAT_2 = 2

RTMP_SIG_SIZE = 1536
SHA256DL = 32

RandomCrud = bytes([
    0xf0, 0xee, 0xc2, 0x4a, 0x80, 0x68, 0xbe, 0xe8,
    0x2e, 0x00, 0xd0, 0xd1, 0x02, 0x9e, 0x7e, 0x57,
    0x6e, 0xec, 0x5d, 0x2d, 0x29, 0x80, 0x6f, 0xab,
    0x93, 0xb8, 0xe6, 0x36, 0xcf, 0xeb, 0x31, 0xae
])

GenuineFMSConst = 'Genuine Adobe Flash Media Server 001'
GenuineFMSConstCrud = bytes(GenuineFMSConst, 'utf8') + RandomCrud

GenuineFPConst = 'Genuine Adobe Flash Player 001'
GenuineFPConstCrud = bytes(GenuineFPConst, 'utf8') + RandomCrud


def calcHmac(data, key):
    return hmac.new(key, data, hashlib.sha256).digest()

def GetClientGenuineConstDigestOffset(buf):
    offset = buf[0] + buf[1] + buf[2] + buf[3]
    offset = (offset % 728) + 12
    return offset

def GetServerGenuineConstDigestOffset(buf):
    offset = buf[0] + buf[1] + buf[2] + buf[3]
    offset = (offset % 728) + 776
    return offset

def detectClientMessageFormat(clientsig):
    sdl = GetServerGenuineConstDigestOffset(clientsig[772:776])
    msg = clientsig[:sdl] + clientsig[sdl + SHA256DL:]
    computedSignature = calcHmac(msg, bytes(GenuineFPConst, 'utf8'))
    providedSignature = clientsig[sdl:sdl + SHA256DL]
    if computedSignature == providedSignature:
        return MESSAGE_FORMAT_2
    sdl = GetClientGenuineConstDigestOffset(clientsig[8:12])
    msg = clientsig[:sdl] + clientsig[sdl + SHA256DL:]
    computedSignature = calcHmac(msg, bytes(GenuineFPConst, 'utf8'))
    providedSignature = clientsig[sdl:sdl + SHA256DL]
    if computedSignature == providedSignature:
        return MESSAGE_FORMAT_1
    return MESSAGE_FORMAT_0

def generateS1(messageFormat):
    randomBytes = bytes([random.randint(0, 255) for _ in range(RTMP_SIG_SIZE - 8)])
    handshakeBytes = bytes([0, 0, 0, 0, 1, 2, 3, 4]) + randomBytes

    if messageFormat == 1:
        serverDigestOffset = GetClientGenuineConstDigestOffset(handshakeBytes[8:12])
    else:
        serverDigestOffset = GetServerGenuineConstDigestOffset(handshakeBytes[772:776])

    msg = handshakeBytes[:serverDigestOffset] + handshakeBytes[serverDigestOffset + SHA256DL:]
    hashValue = calcHmac(msg, bytes(GenuineFMSConst, 'utf8'))
    handshakeBytes = handshakeBytes[:serverDigestOffset] + hashValue + handshakeBytes[serverDigestOffset + SHA256DL:]
    return handshakeBytes

def generateS2(messageFormat, clientsig):
    randomBytes = bytes([random.randint(0, 255) for _ in range(RTMP_SIG_SIZE - 32)])

    if messageFormat == 1:
        challengeKeyOffset = GetClientGenuineConstDigestOffset(clientsig[8:12])
    else:
        challengeKeyOffset = GetServerGenuineConstDigestOffset(clientsig[772:776])

    challengeKey = clientsig[challengeKeyOffset:challengeKeyOffset + 32]
    hashValue = calcHmac(challengeKey, bytes(GenuineFMSConstCrud))
    signature = calcHmac(randomBytes, hashValue)
    s2Bytes = randomBytes + signature
    return s2Bytes

def generateS0S1S2(clientsig):
    clientType = bytes([3])
    messageFormat = detectClientMessageFormat(clientsig)
    if messageFormat == MESSAGE_FORMAT_0:
        allBytes = clientType + clientsig + clientsig
    else:
        s1Bytes = generateS1(messageFormat)
        s2Bytes = generateS2(messageFormat, clientsig)
        allBytes = clientType + s1Bytes + s2Bytes
    return allBytes