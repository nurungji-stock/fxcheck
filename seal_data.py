"""데이터 파일 잠금/풀기 (GitHub Actions에서 fetch.py 앞뒤로 실행)

  python seal_data.py open   → 저장소에 잠겨 있는 json을 풀어서 fetch.py가 읽을 수 있게 함
  python seal_data.py seal   → fetch.py가 만든 json과 차트 파일을 잠가서 저장소에 올림

비밀번호는 GitHub 비밀값 SITE_PASS 에서만 읽음 (저장소 어디에도 안 남음).
홈페이지 잠금 도구(lock.html)와 같은 방식(PBKDF2-SHA256 60만 번 + AES-GCM)이라
같은 비밀번호로 잠근 데이터는 홈페이지에서만 풀림.
"""
import base64, glob, hashlib, json, os, sys

SALT = b"nurungji-site-v1"
ITER = 600000
FILES = ["data.json", "codes.json", "history.json", "earnings.json", "stocks.json", "sectors.json"]


def derive(pw):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), SALT, ITER, 32)


def key():
    pw = os.environ.get("SITE_PASS", "")
    return derive(pw) if pw else None


def old_key():
    pw = os.environ.get("SITE_PASS_OLD", "")
    return derive(pw) if pw else None


def is_env(obj):
    return isinstance(obj, dict) and obj.get("enc") == 1 and "iv" in obj and "ct" in obj


def seal_bytes(k, raw):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(12)
    ct = AESGCM(k).encrypt(iv, raw, None)
    return json.dumps({"enc": 1, "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()},
                      separators=(",", ":"))


def open_bytes(k, env):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(k).decrypt(base64.b64decode(env["iv"]), base64.b64decode(env["ct"]), None)


def main(mode):
    k = key()
    if k is None:
        print("::warning::SITE_PASS 비밀값이 없어서 데이터를 잠그지 않았습니다. 저장소 Settings → Secrets 에 SITE_PASS 를 넣어주세요.")
        return
    targets = [f for f in FILES if os.path.exists(f)]
    if mode == "seal":
        targets += glob.glob("charts_out/c/*.json")
    n = 0
    for f in targets:
        raw = open(f, "rb").read()
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        if mode == "open" and is_env(obj):
            try:
                plain = open_bytes(k, obj)
            except Exception:
                ok = old_key()
                if ok is None:
                    sys.exit(f"{f}: 비밀번호가 맞지 않습니다. 비밀번호를 바꿨다면 예전 비밀번호를 SITE_PASS_OLD 에 넣고 다시 실행하세요.")
                plain = open_bytes(ok, obj)
            open(f, "wb").write(plain); n += 1
        elif mode == "seal" and not is_env(obj):
            open(f, "w", encoding="utf-8").write(seal_bytes(k, raw)); n += 1
    print(mode, n, "개 파일")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "seal")
