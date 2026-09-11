# aXv

aXvは、Windows向けのRust製画像・アーカイブビューアです。フォルダ、単体画像、
ZIP、RAR、7zを表示し、任意の外部ncnn Vulkanエンジンで画像をアップスケールします。

このブランチはRust実装のみを収録しています。

## ビルド

```bat
build_rust.bat
```

Rustを通常インストールしない場合は `build_rust_portable.bat` を実行します。生成物は
`target\release\axv.exe` です。詳しくは [RUST_PORT.md](RUST_PORT.md)を参照してください。

配布用ZIPは `build_release.ps1` で作成します。EXE、SHA-256、依存パッケージ一覧と、
検出したライセンス原文を `release/` 以下へまとめます。

## 任意ランタイムの準備

```bat
setup_optional_tools.bat
```

このバッチは利用者の確認後、MITライセンス等で公開されているncnn Vulkanツールを
公式GitHub Releasesから直接取得して `ai_upscale/` へ展開します。自動取得しないUnRARは
RARLAB公式ページを既定ブラウザで開き、配置先を表示します。

外部ツールはaXvのリポジトリやReleaseへ同梱しません。詳しくは
[AI_SETUP.md](AI_SETUP.md)と[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)を確認してください。
AIツールやUnRARがなくても、通常画像とZIPの閲覧機能は利用できます。

## ライセンス

aXv固有のソースは[Apache License 2.0](LICENSE)です。依存ライブラリと利用者が取得する
外部ツールには、それぞれのライセンスが適用されます。
