# 不同机制来源：成交量分布进入原生账户，主导率规范池未准入

本轮没有采用来源宣传收益。**BTC成交量分布的两种政策已另立冻结协议，完成12个原生资金账户及真实分仓组合，全部晋级失败；新增合格独立alpha为0。** [完整账户报告](../volume_profile_native_20261114_v1/README.md)保留净收益、成本、风险差异及原生执行证据。BTC主导率规范池停在输入准入，资金账户0，收益未知。

## 主导率来源的定义与缺口

固定 [PeterLP123/systematic-crypto-research](https://github.com/PeterLP123/systematic-crypto-research/tree/a16b8c595fecbab40a25b7fdb30652c5bcda6b92)，MIT。读取固定策略规范、构造函数及前推加载器。所谓主导率使用**日收盘价×基础币成交量**在11币中的份额，既不是全市场市值份额，也不是真实逐笔quote volume。不能把这一名称直接当成资金市值轮动因子。

原规范11币含MATIC，现有本地配对研究原生现货缓存只覆盖另10币。通过真正的`load_pair_history(fill_up_missing=False)`逐项检查：10个名字各有原账户窗口608个日观测，MATIC文件缺失、原生返回0行；原冻结历史pickle未取得。这个发现只说明**当前输入合同不完整**，不宣称别处不存在历史，也不证明该经济机制亏损。

另外，仅执行来源纯`build_s2_features`及UTC索引助手的合成探针：45天三资产，最后5天MATIC价格／名义量未知。源函数仍能给出有限BTC份额，MATIC前填后成为0报价收益，等权篮子仍非零。合成结果不能被当作真实可交易性、观察到的零收益或现金替代合同；没有调用来源资金引擎。

完整规范池未准入，**没有改成10币、把POL改名、选择来源24个后验变体中的ETH／BNB／SOL赢家或缩短原账户窗口**。没有新Registry、factor_analysis或资金收益。精确状态在[输入准入](dominance_input_admission.json)；原规范、原源码与抓取SHA保留。

## 成交量分布来源

固定 [VernonCM/bot-volume-profile-bybit](https://github.com/VernonCM/bot-volume-profile-bybit/tree/b213ff69f18bc0907249cbc8a0ea20146ccd199c)。源方法明确是十五分钟K线基础币量在触及价格格间均摊，前一完整UTC日的100格／70%价值区供次日固定使用。不是逐笔成交分布、实际持仓成本或主动买卖差值。

本地独立数学／状态代码精确匹配来源纯函数，通过真实DataProvider及因果前缀检查，再接入原生账户。持仓状态、数量、费用、资金费、精度、五分钟成交与风险始终由Freqtrade所有，未运行来源独立账户模拟器。源树未取得许可证声明，原文保留供本地阅读和纯规则核对，不把来源资金实现复制进产品。

[作者原文](https://criptomatiko.com/volume-profile/)仅用于理解其声明的近似和限制，收益不继承。原文的Bybit历史、入场费用简化及入场柱保护与本地账户不同，差异见资金报告。

## X、YouTube、抖音实际读到了什么

- [X原帖](https://x.com/PPai_Crypto/status/1930172922892615823)：网页读取器403，但普通下载HTTP200并取得原帖／跟帖正文，内容是指标源文件分享。没有读取GoogleDrive文件、完整指标代码或采用交易收益。
- [TradingView官方YouTube教程](https://www.youtube.com/watch?v=PnIS9LZSvFc)：取得播放器作者、标题、描述及字幕轨道；字幕接口200但正文0字节。没有观看或声称读过完整字幕，没有提取新交易规则。
- [抖音索引页](https://www.douyin.com/shipin/7306082904801544226)：网页读取到关联视频的AI文稿，平台展示账户为“追涨老司机”；学习了POC／VAH／VAL和固定、周期、可视范围的区别。关联[原视频页](https://www.douyin.com/video/7224487650969799973)无可读正文，普通下载索引页444拦截。文稿是自动文本，未认证音视频转录准确性，也未观看视频。没有把它当成完整数值策略或据此改100格／70%规则。

从范围可被用户拖动这一描述推断：若事后观察价格才选锚点，会引入历史选择偏差。这是研究输入要求，不是声称视频里的支撑阻力已被验证。以上平台均未提供本地资金绩效证据。

来源：[初次抓取](source_receipts.json)、[规范与加载器](followup_receipts.json)、[社交／作者原文](social_receipts.json)、[YouTube](youtube_receipts.json)、[逐项状态](source_intake.json)。整体更高收益目标仍未完成，无独立样本外、模拟观察或实盘部署。
