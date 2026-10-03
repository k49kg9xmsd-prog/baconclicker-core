# Bacon IPA Injector

把 `BaconClickerCore.dylib` 直接注入 iOS IPA 的簡單網頁工具。

## 檔案結構

這版已把 `index.html` 移到專案根目錄，不再放在 `static/` 或外層 `BaconIPAWeb/` 資料夾裡：

```text
index.html
app.py
injector.py
requirements.txt
Dockerfile
README.md
.gitignore
plugins/
  BaconClickerCore.dylib
```

ZIP 解壓後即可直接看到 `index.html`、`app.py` 等檔案。

## 它做什麼

1. 上傳 `.ipa`
2. 找出 `Payload/*.app/Info.plist` 與主執行檔
3. 將 `BaconClickerCore.dylib` 放進 `Frameworks/`
4. 在 arm64 主程式加入 `@executable_path/Frameworks/BaconClickerCore.dylib`
5. 移除 `_CodeSignature` 資源簽名並重新打包
6. 回傳 `*-BaconInjected-Unsigned.ipa`

輸出的 IPA 必須重新簽名，例如使用 KSign。此工具不需要也不應收集 `.p12` 或憑證密碼。

## 啟動

需要 Python 3.10+：

```bash
python -m pip install -r requirements.txt
python app.py
```

瀏覽器開啟：

```text
http://127.0.0.1:8000
```

> 注意：`index.html` 雖然已在根目錄，但 IPA 注入依然需要 `app.py` 後端。直接雙擊 `index.html` 只能看到介面，無法呼叫 `/api/inject` 完成注入。

## Docker

```bash
docker build -t bacon-ipa-injector .
docker run --rm -p 8000:8000 bacon-ipa-injector
```
