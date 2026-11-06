# 磁盘空间分析报告

- 生成时间：2026-11-06 04:47
- 扫描路径：`C:\`
- 扫描规模：623.6 GB / 3,213,809 个文件 / 513,892 个目录
- 扫描耗时：43.7 秒（862 个目录因权限或占用未能读取）

## 一、总体情况

| 指标 | 数值 |
|---|---|
| 卷容量 | 953.2 GB |
| 已用 | 526.3 GB |
| 剩余 | 426.9 GB |
| 使用率 | 55.2% |

`[█████████████████░░░░░░░░░░░░░]` 55.2%

> **只清垃圾**：可回收约 **36.5 GB**，清理后剩余 463.4 GB（48.6% 可用）
>
> **把大文件挪出 C 盘**：另有 **184.8 GB** 是可归档数据，这部分才是大头，但需要你决定放到哪个盘。

## 二、空间去哪了

| 目录 | 占用 | 占已扫描 | 相对大小 |
|---|---|---|---|
| `Users` | 389.0 GB | 62.4% | `████████████` |
| `D` | 89.6 GB | 14.4% | `███░░░░░░░░░` |
| `Program Files` | 81.2 GB | 13.0% | `███░░░░░░░░░` |
| `Windows` | 27.8 GB | 4.5% | `█░░░░░░░░░░░` |
| `Program Files (x86)` | 12.6 GB | 2.0% | `░░░░░░░░░░░░` |
| `ProgramData` | 3.4 GB | 0.5% | `░░░░░░░░░░░░` |
| `DrvPath` | 1.2 GB | 0.2% | `░░░░░░░░░░░░` |
| `mnt` | 439.0 MB | 0.1% | `░░░░░░░░░░░░` |
| `tmp` | 112.9 MB | 0.0% | `░░░░░░░░░░░░` |
| `E` | 30.7 MB | 0.0% | `░░░░░░░░░░░░` |
| `refprop` | 12.1 MB | 0.0% | `░░░░░░░░░░░░` |
| `down` | 10.1 MB | 0.0% | `░░░░░░░░░░░░` |

### 最大的文件

| 文件 | 大小 | 完整路径 |
|---|---|---|
| `model-a.mph` | 46.3 GB | `C:\Users\me\Desktop\research\model\model-a.mph` |
| `model-b-run2.mph` | 41.0 GB | `C:\Users\me\Desktop\research\model\model-b-run2.mph` |
| `model-b-run1.mph` | 41.0 GB | `C:\Users\me\Desktop\research\model\model-b-run1.mph` |
| `model-b.mph` | 41.0 GB | `C:\Users\me\Desktop\research\model\model-b.mph` |
| `textbooks.zip` | 38.1 GB | `C:\Users\me\Downloads\textbooks.zip` |
| `data.vdi` | 20.7 GB | `C:\D\Program Files\MuMu\MuMuPlayer\vms\MuMuPlayer-15.0-0\data.vdi` |
| `ext4_ubuntu_backup.vhdx` | 14.4 GB | `C:\Users\me\AppData\Local\wsl_backup\ext4_ubuntu_backup.vhdx` |
| `hiberfil.sys` | 12.7 GB | `C:\hiberfil.sys` |
| `COMSOL 6.4.rar` | 12.0 GB | `C:\D\BaiduNetdiskDownload\034 COMSOL 6.4\COMSOL 6.4.rar` |
| `pagefile.sys` | 5.5 GB | `C:\pagefile.sys` |

## 三、能清什么

### 3.1 可直接清理

删了系统或程序会自己重建，不丢数据。 **合计 22.4 GB**

| 项目 | 大小 | 为什么 | 怎么做 |
|---|---|---|---|
| npm 缓存 | 12.2 GB | npm 的本地包缓存。 | spacesift clean --only npm-cache --yes |
| 用户临时文件 | 5.5 GB | 程序和安装包留下的临时文件，重启后大多已经没用。 | spacesift clean --only user-temp --yes |
| uv 缓存 | 1.8 GB | uv 的包缓存，可用 `uv cache clean` 重建。 | spacesift clean --only uv-cache --yes |
| Edge 缓存 | 1012.9 MB | 网页缓存，可安全清理。 | spacesift clean --only browser-cache-edge --yes |
| pip 缓存 | 944.8 MB | 下载过的 wheel 包缓存，重装时会重新下载。 | spacesift clean --only pip-cache --yes |
| Chrome 缓存 | 848.3 MB | 网页缓存，删除后首次访问会稍慢，不影响登录状态。 | spacesift clean --only browser-cache-chrome --yes |
| Electron 应用缓存 | 151.2 MB | VS Code / Slack / Discord 等 Electron 应用的缓存与日志。 | spacesift clean --only electron-cache --yes |
| 缩略图缓存 | 43.1 MB | 资源管理器的缩略图数据库，会自动重建。 | spacesift clean --only thumbnail-cache --yes |
| Windows 错误报告 | 258.5 KB | 错误报告队列，可以安全清空。 | spacesift clean --only wer-reports --yes |
| Windows 更新下载缓存 | 129.3 KB | 已安装更新的安装包缓存，删除后不会影响已装补丁。 | spacesift clean --only update-download --yes |

### 3.2 确认后清理

清了有副作用（要重新下载、丢失回滚点等），先看一眼。 **合计 14.1 GB**

| 项目 | 大小 | 为什么 | 怎么做 |
|---|---|---|---|
| Chrome 端侧 AI 模型 | 11.9 GB | Chrome 内置的端侧 AI 模型，每份约 4 GB，多个浏览器副本各存一份。删了下次用到会重新下载。 | `spacesift clean --only chrome-ondevice-model --include-caution --yes` |
| pnpm 全局存储 | 1.7 GB | pnpm 用硬链接共享这个存储，删了已装项目的依赖会一起坏。 | `spacesift clean --only pnpm-store --include-caution --yes` |
| 组件存储备份 (WinSxS) | 363.5 MB | 微软官方建议用 DISM 而不是手删；手删会破坏组件存储。 | `spacesift clean --only winsxs-backup --include-caution --yes` |
| Windows 日志 | 103.5 MB | 系统安装与排错日志，出问题时还有用，平时可以清。 | spacesift clean --only logs --include-caution --yes |
| 回收站 | 86.0 MB | 回收站里的东西是用户主动删的，但清空后不可恢复。 | `spacesift clean --only recycle-bin --include-caution --yes` |

### 3.3 建议归档到其它盘

不是垃圾，是放错位置的大文件。 **合计 184.8 GB**

| 项目 | 大小 | 为什么 | 怎么做 |
|---|---|---|---|
| 大文件集中地：C:\Users\me\Desktop\research\model | 171.7 GB | 6 个文件超过 1.0 GB，合计 171.7 GB | 移动到 D:/E: 等数据盘，或归档压缩后删除 |
| 大文件集中地：C:\D\BaiduNetdiskDownload\034 COMSOL 6.4 | 12.0 GB | 1 个文件超过 1.0 GB，合计 12.0 GB | 移动到 D:/E: 等数据盘，或归档压缩后删除 |
| 大文件集中地：C:\D\COMSOL64\Multiphysics\ext\dnn\win64 | 1.2 GB | 1 个文件超过 1.0 GB，合计 1.2 GB | 移动到 D:/E: 等数据盘，或归档压缩后删除 |

### 3.4 只能人工处理

工具不会动，给出操作路径。 **合计 98.3 GB**

| 项目 | 大小 | 为什么 | 怎么做 |
|---|---|---|---|
| 下载文件夹 | 42.1 GB | 里面是你的文件，工具不会动，只报告大小。 | `按修改时间手动挑旧文件删` |
| 微信聊天文件 | 22.6 GB | 微信接收的图片/视频/文件，往往几十 GB。删了聊天记录里的附件就打不开了。 | `微信 → 设置 → 存储空间 → 管理，按聊天清理` |
| WSL 备份与磁盘镜像 | 14.4 GB | WSL 的 ext4.vhdx 与备份文件通常十几 GB，删了等于删掉整个 Linux 环境。 | `迁移：wsl --export 到 D 盘后 --unregister 再 --import` |
| 休眠文件 (hiberfil.sys) | 12.7 GB | 快速启动和休眠用的内存镜像，通常等于内存大小。关掉休眠可立即回收。 | `powercfg /h off   # 需要管理员，关掉后「休眠」菜单消失` |
| 页面文件 (pagefile.sys) | 5.5 GB | 虚拟内存交换文件，不能直接删，只能在系统属性里调小或移到别的盘。 | `系统属性 → 高级 → 性能设置 → 高级 → 虚拟内存` |
| QQ 接收文件 | 988.2 MB | QQ 保存的聊天文件与图片。 | `QQ → 设置 → 文件管理 → 清理` |

## 四、推荐执行顺序

1. **第一步：清缓存与临时文件** —— 约 22.4 GB。全程自动，删了系统自己重建
   - 累计可回收：22.4 GB
2. **第二步：确认后清** —— 约 14.1 GB。看一眼清单再决定，收益不如第一步稳定
   - 累计可回收：36.5 GB
3. **第三步：把大文件挪出 C 盘** —— 约 184.8 GB。收益最大的一步，需要你自己选目标盘
   - 累计可回收：221.3 GB
4. **第四步：系统级开关** —— 约 98.3 GB。休眠文件、页面文件、微信缓存等，需要手动操作
   - 累计可回收：319.7 GB

工具直接执行的命令：

```bash
# 先看不动手（默认就是 dry-run）
spacesift clean

# 确认后真清，只清安全项
spacesift clean --yes

# 连「确认后清理」的一起清
spacesift clean --yes --include-caution
```

## 五、注意

- 清缓存后首次启动程序、首次访问网页会变慢，属于正常现象。
- `Windows 更新下载缓存`、`组件存储备份`、`系统还原点` 需要管理员权限；后者请用微软官方命令（DISM / vssadmin），不要手删。
- `休眠文件`、`页面文件` 不是垃圾，是系统功能占位；关掉休眠会同时失去「快速启动」。
- 归档建议里的文件属于个人数据，工具永远不会自动删除。

---

由 [SpaceSift](https://github.com/Twofruitsgrape/spacesift) 生成。