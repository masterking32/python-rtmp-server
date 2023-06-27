def decodeAMF0Cmd(dbuf):
    buffer = dbuf
    resp = {}

    cmd = amf0DecodeOne(buffer)
    resp["cmd"] = cmd["value"]
    buffer = buffer[cmd["len"]:]

    if cmd["value"] in rtmpCmdCode:
        for n in rtmpCmdCode[cmd["value"]]:
            if len(buffer) > 0:
                r = amf0DecodeOne(buffer)
                buffer = buffer[r["len"]:]
                resp[n] = r["value"]
    else:
        Logger.error("Unknown command", resp)

    return resp
