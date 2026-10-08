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

