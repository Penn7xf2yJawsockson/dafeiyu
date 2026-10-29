# 查看、修改、删除记忆

大肥鱼的记忆在两个库里:**Hindsight** 记事实(「用户下周六去南昌看演唱会」),**Ombre** 记经历与感受(一段一段的小故事)。网关自己不存记忆,只记每一轮的原话。两个库都有自带的网页界面,平时关着,要用时打开「管理模式」。

## 打开和关闭管理模式

在 WSL 里,进入仓库目录:

```bash
cd ~/dafeiyu
docker compose -f compose.yml -f deploy/compose.admin.yml up -d   # 打开
docker compose up -d                                              # 用完关闭
```

打开后,在电脑浏览器里访问(只有这台电脑能打开,手机和局域网都访问不到):

| 界面 | 地址 | 登录口令(在 `.env` 里) |
|---|---|---|
| Hindsight 控制台 | http://127.0.0.1:9999 | `HINDSIGHT_CP_ACCESS_KEY` |
| Ombre Dashboard | http://127.0.0.1:18001 | `OMBRE_DASHBOARD_PASSWORD` |

查看口令:`grep -E '^(HINDSIGHT_CP_ACCESS_KEY|OMBRE_DASHBOARD_PASSWORD)=' .env`

管理模式开着时,Kelivo 和 DSH 照常聊天。打开和关闭时两个记忆库会重启几秒,这期间发的消息会降级(没有记忆),回复照常。

## Hindsight:事实

1. 登录后选记忆库 **dafeiyu**(界面带中文翻译,浏览器语言是中文时会自动用中文)(`contract-test` 是测试用的,不用管)。
2. 进入记忆列表,可以搜索、按时间筛选。每条记忆有类型:
   - **world**:关于你和世界的事实
   - **experience**:大肥鱼自己做过、说过的事
   - **observation**:Hindsight 把多条相近事实合并出的结论,**不能直接改**
3. 点开一条 world 或 experience 记忆,在「整理」里:
   - **编辑**:改错字、改错的内容。保存后 Hindsight 会重新计算,由它合并出的 observation 也会跟着更新。
   - **失效**:不再出现在召回里,但保留着,随时能**恢复**。这是 Hindsight 里的「删除」,建议优先用它。
4. observation 不对,就去改或者失效它背后的那几条原始事实,observation 会自动重新生成。

要注意:如果某一段对话以后被网关整段重写(只在修数据时发生),这一段产生的事实会整体重新抽取,你手动改过的会被覆盖。正常聊天不会触发。

## Ombre:经历与感受

1. 登录后进入「记忆」页,能看到所有记忆桶,可以按主题、时间筛选。
2. 对一条记忆:
   - **编辑**:直接改正文。保存后会重新计算检索用的向量。
   - **主动遗忘**:不再在每段对话开头主动浮现,也不会被网关每轮的检索带出来;记忆本身还在,再点一次就取消。想让它「先别提了」用这个。
   - **pin(置顶)** / **resolve(放下)**:按需使用。
3. **想彻底拿掉一条**,不要用 Dashboard 的「归档」按钮。那个按钮只会提一个「删除申请」,要大肥鱼自己用工具批准,而聊天时她拿不到这个工具,申请会一直挂着。改用脚本(不需要打开管理模式):

   ```bash
   deploy/ombre-archive.sh 9fc1d4b0958a            # 归档,可以一次写多个 ID
   deploy/ombre-archive.sh --restore 9fc1d4b0958a  # 恢复
   ```

   ID 是 Dashboard 里每条记忆的 12 位编号。归档后文件仍在 `data/ombre/archive/`,网关不会再把它召回。

## 怎么找到要改的那条

- 大肥鱼说错了一件事:在 Hindsight 里搜那件事的关键词,通常就能找到对应的事实。
- 想知道某一轮她「看到」了哪些记忆:网关的管理接口记着每轮的召回块,

  ```bash
  T=$(grep '^GATEWAY_ADMIN_TOKEN=' .env | cut -d= -f2-)
  curl -s -H "Authorization: Bearer $T" http://127.0.0.1:18080/admin/audit | python3 -m json.tool | tail -30
  ```

  `memory_ids` 里 `facts:` 开头的是 Hindsight 记忆 ID,`affect:` / `surfaced:` 开头的是 Ombre 桶 ID。
