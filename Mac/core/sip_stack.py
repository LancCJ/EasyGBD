# sip_stack.py
import hashlib
import re
import random
import string

XML_ENCODING = "gbk"

def _rand_token(n=8):
    """生成纯字母数字随机token，用于 branch / tag 等不允许含 '@' 的 SIP 参数"""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=n))

def get_md5(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def generate_auth_response(username, password, realm, nonce, uri, method="REGISTER"):
    ha1 = get_md5(f"{username}:{realm}:{password}")
    ha2 = get_md5(f"{method}:{uri}")
    response = get_md5(f"{ha1}:{nonce}:{ha2}")
    return response

def build_register_msg(config, cseq, call_id, auth_header=None, expires=None):
    uri = f"sip:{config.SIP_SERVER_DOMAIN}@{config.SIP_SERVER_IP}:{config.SIP_SERVER_PORT}"
    to_uri = f"sip:{config.DEVICE_ID}@{config.SIP_SERVER_IP}:{config.SIP_SERVER_PORT}"
    from_uri = f"sip:{config.DEVICE_ID}@{config.LOCAL_IP}:{config.LOCAL_PORT}"
    contact_uri = f"sip:{config.DEVICE_ID}@{config.LOCAL_IP}:{config.LOCAL_PORT}"

    msg = f"REGISTER {uri} SIP/2.0\r\n"
    msg += f"Via: SIP/2.0/UDP {config.LOCAL_IP}:{config.LOCAL_PORT};rport;branch=z9hG4bK{_rand_token(16)}\r\n"
    msg += f"From: <{from_uri}>;tag={_rand_token(8)}\r\n"
    msg += f"To: <{to_uri}>\r\n"
    msg += f"Call-ID: {call_id}\r\n"
    msg += f"CSeq: {cseq} REGISTER\r\n"
    msg += f"Contact: <{contact_uri}>\r\n"
    msg += f"Max-Forwards: 70\r\n"
    msg += f"User-Agent: {config.MANUFACTURER}\r\n"
    
    expires_val = expires if expires is not None else getattr(config, 'EXPIRE_TIME', 3600)
    msg += f"Expires: {expires_val}\r\n"

    if auth_header:
        msg += f"Authorization: {auth_header}\r\n"

    msg += "Content-Length: 0\r\n\r\n"
    return msg


def build_device_info_msg(config, cseq, call_id, sn=None):
    """注册成功后主动推送或应答 DeviceInfo，携带厂商/型号/名称等元信息"""
    uri = f"sip:{config.SIP_SERVER_DOMAIN}@{config.SIP_SERVER_IP}:{config.SIP_SERVER_PORT}"
    to_uri = f"sip:{config.SIP_SERVER_ID}@{config.SIP_SERVER_IP}:{config.SIP_SERVER_PORT}"
    from_uri = f"sip:{config.DEVICE_ID}@{config.LOCAL_IP}:{config.LOCAL_PORT}"

    sn_val = sn if sn is not None else cseq
    xml_body = f"""<?xml version="1.0" encoding="GB2312"?>
<Response>
<CmdType>DeviceInfo</CmdType>
<SN>{sn_val}</SN>
<DeviceID>{config.DEVICE_ID}</DeviceID>
<DeviceName>{getattr(config, 'DEVICE_NAME', config.MANUFACTURER)}</DeviceName>
<Manufacturer>{getattr(config, 'MANUFACTURER', 'lanccj')}</Manufacturer>
<Model>{getattr(config, 'MODEL', 'MacSimulator')}</Model>
<Firmware>V1.0</Firmware>
<MaxCamera>1</MaxCamera>
<MaxAlarm>0</MaxAlarm>
</Response>"""

    xml_bytes = xml_body.encode(XML_ENCODING)
    msg = f"MESSAGE {uri} SIP/2.0\r\n"
    msg += f"Via: SIP/2.0/UDP {config.LOCAL_IP}:{config.LOCAL_PORT};rport;branch=z9hG4bK{_rand_token(16)}\r\n"
    msg += f"From: <{from_uri}>;tag={_rand_token(8)}\r\n"
    msg += f"To: <{to_uri}>\r\n"
    msg += f"Call-ID: {call_id}\r\n"
    msg += f"CSeq: {cseq} MESSAGE\r\n"
    msg += "Content-Type: Application/MANSCDP+xml\r\n"
    msg += f"Max-Forwards: 70\r\n"
    msg += f"User-Agent: {getattr(config, 'MANUFACTURER', 'OpenGBD')}\r\n"
    msg += f"Content-Length: {len(xml_bytes)}\r\n\r\n"
    msg += xml_body
    return msg

def build_keepalive_msg(config, cseq, call_id):
    uri = f"sip:{config.SIP_SERVER_DOMAIN}@{config.SIP_SERVER_IP}:{config.SIP_SERVER_PORT}"
    to_uri = f"sip:{config.SIP_SERVER_ID}@{config.SIP_SERVER_IP}:{config.SIP_SERVER_PORT}"
    from_uri = f"sip:{config.DEVICE_ID}@{config.LOCAL_IP}:{config.LOCAL_PORT}"

    xml_body = f"""<?xml version="1.0" encoding="GB2312"?>
<Notify>
<CmdType>Keepalive</CmdType>
<SN>{cseq}</SN>
<DeviceID>{config.DEVICE_ID}</DeviceID>
<Status>OK</Status>
<Info>
</Info>
</Notify>"""

    msg = f"MESSAGE {uri} SIP/2.0\r\n"
    msg += f"Via: SIP/2.0/UDP {config.LOCAL_IP}:{config.LOCAL_PORT};rport;branch=z9hG4bK{_rand_token(16)}\r\n"
    msg += f"From: <{from_uri}>;tag={_rand_token(8)}\r\n"
    msg += f"To: <{to_uri}>\r\n"
    msg += f"Call-ID: {call_id}\r\n"
    msg += f"CSeq: {cseq} MESSAGE\r\n"
    msg += "Content-Type: Application/MANSCDP+xml\r\n"
    msg += f"Max-Forwards: 70\r\n"
    msg += f"User-Agent: {config.MANUFACTURER}\r\n"
    msg += f"Content-Length: {len(xml_body.encode(XML_ENCODING))}\r\n\r\n"
    msg += xml_body
    return msg

def build_catalog_response_msg(config, sn, cseq, call_id):
    uri = f"sip:{config.SIP_SERVER_DOMAIN}@{config.SIP_SERVER_IP}:{config.SIP_SERVER_PORT}"
    to_uri = f"sip:{config.SIP_SERVER_ID}@{config.SIP_SERVER_IP}:{config.SIP_SERVER_PORT}"
    from_uri = f"sip:{config.DEVICE_ID}@{config.LOCAL_IP}:{config.LOCAL_PORT}"
    channels = getattr(config, 'channels', None) or [{
        "channel_id": config.CHANNEL_ID,
        "name": "MacCameraPreview",
    }]
    items = []
    for idx, channel in enumerate(channels):
        channel_id = channel.get("channel_id", config.CHANNEL_ID)
        channel_name = channel.get("name") or f"通道{idx + 1}"
        items.append(f"""<Item>
<DeviceID>{channel_id}</DeviceID>
<Name>{channel_name}</Name>
<Manufacturer>{config.MANUFACTURER}</Manufacturer>
<Model>MacSimulator</Model>
<Owner>{getattr(config, 'OWNER', 'Owner')}</Owner>
<CivilCode>{getattr(config, 'CIVIL_CODE', 'CivilCode')}</CivilCode>
<Address>{getattr(config, 'ADDRESS', 'Address')}</Address>
<Parental>0</Parental>
<ParentID>{config.DEVICE_ID}</ParentID>
<SafetyWay>0</SafetyWay>
<RegisterWay>1</RegisterWay>
<Secrecy>0</Secrecy>
<Status>ON</Status>
</Item>""")
    items_xml = "\n".join(items)

    xml_body = f"""<?xml version="1.0" encoding="GB2312"?>
<Response>
<CmdType>Catalog</CmdType>
<SN>{sn}</SN>
<DeviceID>{config.DEVICE_ID}</DeviceID>
<SumNum>{len(channels)}</SumNum>
<DeviceList Num="{len(channels)}">
{items_xml}
</DeviceList>
</Response>"""

    msg = f"MESSAGE {uri} SIP/2.0\r\n"
    msg += f"Via: SIP/2.0/UDP {config.LOCAL_IP}:{config.LOCAL_PORT};rport;branch=z9hG4bK{_rand_token(16)}\r\n"
    msg += f"From: <{from_uri}>;tag={_rand_token(8)}\r\n"
    msg += f"To: <{to_uri}>\r\n"
    msg += f"Call-ID: {call_id}\r\n"
    msg += f"CSeq: {cseq} MESSAGE\r\n"
    msg += "Content-Type: Application/MANSCDP+xml\r\n"
    msg += f"Max-Forwards: 70\r\n"
    msg += f"User-Agent: {config.MANUFACTURER}\r\n"
    msg += f"Content-Length: {len(xml_body.encode(XML_ENCODING))}\r\n\r\n"
    msg += xml_body
    return msg

def parse_sip_msg(msg_text):
    lines = msg_text.split('\r\n')
    if not lines:
        return None
    
    first_line = lines[0]
    headers = {}
    body = ""
    is_body = False
    
    for line in lines[1:]:
        if line == "" and not is_body:
            is_body = True
            continue
        if is_body:
            body += line + "\r\n"
        else:
            if ":" in line:
                key, val = line.split(":", 1)
                headers[key.strip()] = val.strip()

    return {
        "first_line": first_line,
        "headers": headers,
        "body": body.strip()
    }


def get_header(parsed_msg, name, default=""):
    """按SIP规范以大小写不敏感方式读取头字段。"""
    wanted = name.lower()
    for key, value in parsed_msg.get("headers", {}).items():
        if key.lower() == wanted:
            return value
    return default
