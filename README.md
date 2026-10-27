# 我的设备电量 / My Battery Viewer

Windows 桌面小工具：一眼看到 **索尼 WH-1000XM4 耳机、Xbox Series 手柄、
Hi75C Pro 键盘** 三台设备的电量。

**零第三方运行时依赖**：核心读取层纯 Python + ctypes 直调 Windows API；
界面是本地 Web 前端（标准库 `http.server` + Edge/Chrome 应用窗口）。
打包用 PyInstaller（仅构建期需要）。

## 快速开始

```bat
:: 方式一：直接用打包好的 exe（dist\设备电量.exe）
::   双击 = 面板窗口 + 托盘图标；再次双击 = 唤起新窗口；托盘右键可退出

:: 方式二：源码运行
python tray_app.py            :: 面板窗口 + 托盘
python tray_app.py --tray-only :: 只驻留托盘（开机自启用）
python tray_app.py --panel    :: 只开面板窗口
python devices.py             :: 命令行读一次
python probe.py               :: 排查问题 / 适配新设备
```

## 界面

**Web 面板**：暗色玻璃质感三卡片，每张卡片有设备图标、连接方式、
大号百分比（颜色随电量）、流光进度条、数据来源说明；顶部可切自动刷新
与间隔（15 秒 ~ 10 分钟）；低电量 (<30%) 卡片带红光呼吸提示。
本地 HTTP 服务只绑 127.0.0.1 固定端口（8899，被占自动顺延），不对外网开放。

窗口尺寸：应用窗口使用独立的 Edge 配置目录（`%LOCALAPPDATA%\MyBatteryViewer\edge-profile`，
与个人浏览器数据完全隔离），初始 760×500；你手动调整过的大小和位置
会被记住，之后每次打开自动恢复。

**托盘**：一个图标，数字 = 三台中**最低**的电量，颜色随档位变化。
**悬停** tooltip 一行列出三台各自电量；**右键**弹菜单逐条显示三台电量，
附「立即刷新 / 打开面板 / 退出」；**左键**立即刷新。

## 三台设备分别怎么读到电量

| 设备 | 连接方式 | 读取通道 | 精度 |
|---|---|---|---|
| 索尼 WH-1000XM4 | 蓝牙 | HFP 免提节点上的未公开 PnP 电量属性（`{104EA319-…},2`） | 1% |
| Xbox Series 手柄 | 蓝牙 | BLE 节点 GATT 电量属性（同一个 PnP 属性） | 1% |
| Xbox Series 手柄 | 接收器 / USB | `xinput1_4.dll` 的 `XInputGetBatteryInformation` | 4 档估算 |
| Hi75C Pro 键盘 | 2.4G 接收器 | 接收器（`3554:FA09`）vendor 接口 `0xFF04` 的 **Feature Report #6**，第 4 字节为电量 | 1% |

几个说明：

* **耳机 / 手柄的蓝牙电量**来自 Windows 蓝牙栈自己维护的设备属性 —— 系统
  「设置 → 蓝牙和其他设备」里显示的百分比就是同一个来源，本工具只是把它
  读出来放到桌面 / 托盘。
* **在线判定（防缓存误报）**：BLE 设备的电量属性在设备关机后仍留缓存
  （手柄明明关机也会显示上次的 100%）。本工具做了三层校验：蓝牙栈的
  `fConnected` 标志 + 手柄的 HID 游戏手柄接口存在性（只在连接期间存在）+
  从接口沿设备树精确追溯在线那台的 MAC，只采信在线记录的电量。
* **手柄走 USB 或无线接收器时** XInput 只能拿到「空 / 低 / 中 / 满」四档，
  界面上按档位估算为百分比并会注明；走蓝牙时是 HID 游戏手柄，精确到 1%。
* **键盘的 Feature Report 协议**是拿真实设备探测出来的：满电时接收器对
  Report #6 稳定返回 `06 00 02 64 64 64 6B 4C`。如果以后发现电量对不上，
  跑 `python probe.py keyboard` 对比不同电量下的原始字节，改 `devices.py`
  顶部的 `KEYBOARD_LEVEL_BYTE` 一行即可。
* 键盘插着 USB 线（有线模式）时接收器接口同样存在，读数照常。

## 三种使用形态

1. **Web 面板**：暗色玻璃质感三卡片，大号百分比 + 流光进度条 + 数据来源，
   可选 15 秒 ~ 10 分钟自动刷新，低电量卡片红光呼吸提示。
2. **托盘常驻**（`tray_app.py --tray-only`）：一个托盘图标，数字显示三台中
   **最低**的电量，颜色随电量变化（绿 ≥60 / 黄 30-59 / 红 <30 / 灰=全部未连接）。
   **悬停** tooltip 一行列出三台各自电量；**右键**弹菜单逐条显示三台电量，
   附「立即刷新 / 打开面板 / 退出」；**左键**立即刷新。
3. **命令行**（`devices.py`）。

面板里可以一键开启**开机自启**（往「启动」文件夹放一个
`.lnk` 快捷方式，静默驻留托盘，任务管理器里可见可禁用）。

## 打包成 exe

```bat
pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --noconsole ^
  --name "设备电量" --icon app.ico ^
  --add-data "static;static" tray_app.py
:: 产物在 dist\设备电量.exe
```

exe 是单文件，双击即用，不依赖本机 Python：双击 → 托盘图标 + 面板窗口
一起出现；再次双击只唤起新窗口；托盘右键 → 退出。

## 文件结构

```
my-battery-viewer/
├── hid_win.py          HID 底层：枚举接口 + 读 Feature Report（纯 ctypes）
├── bt_battery.py       蓝牙电量：BTHENUM(HFP) + BTHLE(GATT) 节点的 PnP 属性
├── xinput_battery.py   Xbox 手柄：XInput 1.4 电池信息
├── devices.py          三台设备的统一读取层（换设备改这里的常量）
├── webserver.py        本地 HTTP 服务 + /api/battery JSON 接口
├── webui.py            Edge/Chrome --app 模式弹出应用窗口
├── static/             Web 前端（index.html / style.css / app.js）
├── panel.py            旧版 tkinter 面板（备用）
├── tray_app.py         托盘常驻 + 主入口（SetTimer 驱动，空闲几乎不占 CPU）
├── tray_common.py      配置 / 开机自启 / 单实例
├── win_icon.py         托盘电量数字图标（GDI 绘制，无 Pillow）
├── app.ico             应用图标
└── probe.py            探测调试工具（适配新设备用）
```

## 换设备 / 加设备

`devices.py` 顶部的一组常量就是全部设备定义：

```python
HEADPHONE_NAME_KEYS = ("WH-1000XM4",)          # 蓝牙名字关键词
CONTROLLER_NAME_KEYS = ("Xbox Wireless Controller", "Xbox 手柄")
KEYBOARD_VID = 0x3554                          # 2.4G 接收器 VID
KEYBOARD_USAGE_PAGE = 0xFF04                   # vendor 电量接口
KEYBOARD_REPORT_ID = 6                         # Feature Report 编号
KEYBOARD_LEVEL_BYTE = 3                        # 电量在报文里的字节下标
```

* 蓝牙类设备（耳机 / 手柄 / 音箱）：改名字关键词即可，电量走系统属性，
  通用性很好 —— 换成 WH-1000XM5、DualSense 都只是换个名字的事。
* HID 类设备（键盘 / 鼠标）：用 `python probe.py` 先探测协议（枚举接口、
  逐个 Report ID 读 Feature Report、找哪个字节随电量变化），再填常量。

## 隐私与资源

* 不联网、不上传、默认不写任何日志（`tray_app.py --debug` 才输出到控制台）。
* 托盘用 SetTimer 驱动，空闲时几乎不占 CPU。
* 配置文件在 `%LOCALAPPDATA%\MyBatteryViewer\config.json`。

## 已知坑（写给自己 / 想改代码的人）

* ctypes 定义 `HIDP_CAPS` 等 Windows 结构体**不能截短**——API 会写满
  整个结构体，短了就是堆损坏 + 随机 segfault（本项目的第一个 bug）。
* tk 不是线程安全的：设备读取放后台线程，结果一律走队列回主线程刷 UI。
* 托盘窗口销毁后必须 `PostQuitMessage(0)`，否则进程残留还占着单实例锁。
* explorer 重启后托盘图标会消失，要监听 `TaskbarCreated` 消息重新挂。
* 鼠标 / 键盘的顶层 HID 集合被系统独占，用 `dwDesiredAccess=0` 打开
  依然可以读属性和 Feature Report。

## 参考

项目结构借鉴了 [2.4G-battery-viewer](https://github.com/yszyszysz233/2.4G-battery-viewer)
（读 ATK 鼠标 + DARKRIM 耳机的电量工具），读取通道全部换成了本机三台
设备实测出的协议，代码为本项目独立实现。仅供学习与个人使用。
