# Bacon IPA Injector

把 `BaconClickerCore.dylib` 直接注入 iOS IPA 的簡單網頁工具。

## 它做什麼

1. 上傳 `.ipa`
2. 找出 `Payload/*.app/Info.plist` 與主執行檔
3. 將 `BaconClickerCore.dylib` 放進 `Frameworks/`
4. 在 arm64 主程式加入：
   `@executable_path/Frameworks/BaconClickerCore.dylib`
5. 移除 `_CodeSignature` 資源簽名並重新打包
6. 回傳 `*-BaconInjected-Unsigned.ipa`

輸出的 IPA **必須重新簽名**，例如使用 KSign。此工具不需要也不應收集 `.p12` 或憑證密碼。

## 啟動

需要 Python 3.10+。

```bash
python -m pip install -r requirements.txt
python app.py
```

瀏覽器打開：

```text
http://127.0.0.1:8000
```

若要從同一 Wi‑Fi 的 iPhone 使用，讓電腦防火牆允許 TCP 8000，然後在 iPhone 開：

```text
http://<電腦區網 IP>:8000
```

## Docker

```bash
docker build -t bacon-ipa-injector .
docker run --rm -p 8000:8000 bacon-ipa-injector
```

## 限制

- 目前以 arm64 / arm64 Universal Mach-O 為主。
- 主程式的 Mach-O header 必須有足夠的 load-command padding；若不足會安全停止，不會硬覆寫。
- 某些 App 具有額外完整性驗證、加密、特殊 framework 依賴或反竄改機制，注入後可能無法正常啟動。
- 插件若另有依賴 dylib/framework，需要一併處理；目前內建的是已實機驗證可直接載入的 BaconClicker Core。
