"""UI translations: Russian (default), English, Chinese."""

LANGS = ("ru", "en", "zh")
DEFAULT_LANG = "ru"

LANG_NAME = {"ru": "Русский", "en": "English", "zh": "中文"}

STRINGS: dict[str, dict[str, str]] = {
    # ----- menu buttons -----
    "btn_bal": {
        "ru": "💰 Баланс",
        "en": "💰 Balance",
        "zh": "💰 余额",
    },
    "btn_dep": {
        "ru": "💳 Пополнить",
        "en": "💳 Deposit",
        "zh": "💳 充值",
    },
    "btn_tip": {
        "ru": "💸 Чаевые",
        "en": "💸 Tips",
        "zh": "💸 打赏",
    },
    "btn_ask": {
        "ru": "🧠 ИИ-ассистент",
        "en": "🧠 AI assistant",
        "zh": "🧠 AI 助手",
    },
    "btn_markets": {
        "ru": "📈 Рынки",
        "en": "📈 Markets",
        "zh": "📈 预测市场",
    },
    "btn_bets": {
        "ru": "🎲 Ставки",
        "en": "🎲 Bets",
        "zh": "🎲 投注",
    },
    "btn_donate": {
        "ru": "💛 Донаты",
        "en": "💛 Donate page",
        "zh": "💛 捐赠页",
    },
    "btn_top": {
        "ru": "🏆 Топ",
        "en": "🏆 Top users",
        "zh": "🏆 排行榜",
    },
    "btn_hist": {
        "ru": "🧾 История",
        "en": "🧾 History",
        "zh": "🧾 历史记录",
    },
    "btn_stats": {
        "ru": "📊 Статы",
        "en": "📊 Stats",
        "zh": "📊 统计",
    },
    "btn_wallet": {
        "ru": "👛 Кошелёк",
        "en": "👛 Wallet",
        "zh": "👛 钱包",
    },
    "btn_paywall": {
        "ru": "🔐 Платные посты",
        "en": "🔐 Paid posts",
        "zh": "🔐 付费内容",
    },
    "btn_settings": {
        "ru": "⚙️ Настройки",
        "en": "⚙️ Settings",
        "zh": "⚙️ 设置",
    },
    "btn_about": {
        "ru": "ℹ️ О боте",
        "en": "ℹ️ About",
        "zh": "ℹ️ 关于",
    },
    # ----- common -----
    "menu_balance": {
        "ru": "💰 Баланс: <b>{bal} USDC</b>",
        "en": "💰 Balance: <b>{bal} USDC</b>",
        "zh": "💰 余额：<b>{bal} USDC</b>",
    },
    # ----- settings -----
    "set_title": {
        "ru": "⚙️ <b>Настройки</b>",
        "en": "⚙️ <b>Settings</b>",
        "zh": "⚙️ <b>设置</b>",
    },
    "set_react": {
        "ru": "⚡ <b>Реакции-чаевые</b> — {state}.\nКогда ставишь реакцию на сообщение — автору начисляются USDC.",
        "en": "⚡ <b>Reaction tips</b> — {state}.\nWhen you react to a message, its author receives USDC.",
        "zh": "⚡ <b>表情打赏</b> — {state}。\n当你给消息添加表情时，作者会收到 USDC。",
    },
    "set_notif": {
        "ru": "🔔 <b>Уведомления о депозитах</b> — {state}.\nСообщение при зачислении депозита.",
        "en": "🔔 <b>Deposit notifications</b> — {state}.\nA message when a deposit is credited.",
        "zh": "🔔 <b>充值通知</b> — {state}。\n充值到账时发送消息通知。",
    },
    "on": {"ru": "включены", "en": "on", "zh": "已开启"},
    "off": {"ru": "выключены", "en": "off", "zh": "已关闭"},
    "btn_lang": {
        "ru": "🌐 Язык: {name}",
        "en": "🌐 Language: {name}",
        "zh": "🌐 语言：{name}",
    },
    "btn_back": {
        "ru": "◀️ В меню",
        "en": "◀️ Back to menu",
        "zh": "◀️ 返回菜单",
    },
    # ----- language screen -----
    "lang_title": {
        "ru": "🌐 <b>Выбери язык</b>",
        "en": "🌐 <b>Choose a language</b>",
        "zh": "🌐 <b>选择语言</b>",
    },
    "lang_set": {
        "ru": "✅ Язык изменён: <b>{name}</b>",
        "en": "✅ Language changed: <b>{name}</b>",
        "zh": "✅ 语言已切换：<b>{name}</b>",
    },
    # ----- about -----
    "about_title": {
        "ru": "ℹ️ <b>Что такое Tippy — простыми словами</b>",
        "en": "ℹ️ <b>What is Tippy — in plain words</b>",
        "zh": "ℹ️ <b>Tippy 是什么 —— 简单来说</b>",
    },    "about_body": {
        "ru": (
            "💵 <b>Это деньги для чатов.</b>\n"
            "Внутри бота у тебя свой кошелёк с монетами USDC — как мелочь в кармане, "
            "только в Telegram и в сети Base.\n\n"
            "💸 <b>Благодари деньгами.</b>\n"
            "Кинул другу /tip — он получил USDC. Поставил 🔥 на классное сообщение — "
            "автор получил чаевые. Всё за пару секунд, без карт и банков.\n\n"
            "🎯 <b>Играй и выигрывай.</b>\n"
            "Ставь на исходы событий в /bets, торгуй на рынках предсказаний /markets — "
            "угадал, забрал больше.\n\n"
            "🔓 <b>Это твой кошелёк.</b>\n"
            "Ключ только у тебя (/wallet export), вывод куда угодно (/withdraw). "
            "Все операции видны в блокчейне Base — ничего не спрятано.\n\n"
            "🟦 Base — быстрая и дешёвая сеть от Coinbase. Комиссии — копейки."
        ),
        "en": (
            "💵 <b>It's money for chats.</b>\n"
            "Inside the bot you get your own USDC wallet — like loose change in your "
            "pocket, only inside Telegram, living on the Base network.\n\n"
            "💸 <b>Say thanks with money.</b>\n"
            "Send /tip to a friend — they get USDC. React 🔥 to a great message — "
            "its author gets a tip. Takes seconds, no cards or banks involved.\n\n"
            "🎯 <b>Play and win.</b>\n"
            "Back your predictions in /bets, trade on prediction markets in /markets — "
            "guess right and take the pot.\n\n"
            "🔓 <b>The wallet is yours.</b>\n"
            "Only you hold the key (/wallet export), withdraw anywhere (/withdraw). "
            "Every move is visible on the Base blockchain — nothing hidden.\n\n"
            "🟦 Base is Coinbase's fast and cheap network. Fees are pennies."
        ),
        "zh": (
            "💵 <b>这是聊天里的钱。</b>\n"
            "在机器人里你有自己的 USDC 钱包——就像口袋里的零钱，只不过住在 Telegram 里，"
            "运行在 Base 网络上。\n\n"
            "💸 <b>用金钱表达感谢。</b>\n"
            "给朋友发 /tip——他立刻收到 USDC。给精彩的消息点个 🔥——作者收到打赏。"
            "几秒钟搞定，不需要银行卡和银行。\n\n"
            "🎯 <b>边玩边赢。</b>\n"
            "在 /bets 押注事件结果，在 /markets 预测市场交易——猜对了就能赢走奖池。\n\n"
            "🔓 <b>钱包属于你。</b>\n"
            "钥匙只在你手里（/wallet export），可以提到任何地方（/withdraw）。"
            "每笔操作都能在 Base 区块链上查到——没有任何隐瞒。\n\n"
            "🟦 Base 是 Coinbase 推出的快速且便宜的网络，手续费只要几分钱。"
        ),
    },
    # ----- start -----
    "start_hi": {
        "ru": "👋 Привет, <b>@{name}</b>!",
        "en": "👋 Hi, <b>@{name}</b>!",
        "zh": "👋 你好，<b>@{name}</b>！",
    },
    "start_intro": {
        "ru": "🟦 <b>Tippy</b> — USDC-экономика прямо в Telegram:\n💸 чаевые и реакции · 🎁 донат-страницы · 🎯 рынки предсказаний",
        "en": "🟦 <b>Tippy</b> — a USDC economy right inside Telegram:\n💸 tips and reactions · 🎁 donate pages · 🎯 prediction markets",
        "zh": "🟦 <b>Tippy</b> —— Telegram 里的 USDC 经济：\n💸 打赏与表情 · 🎁 捐赠页 · 🎯 预测市场",
    },
    "start_try": {
        "ru": "Что попробовать?\n• <b>/deposit</b> — пополнить (QR)\n• <b>/bets</b> — поставить на рынок\n• <b>/tip 1 @ник</b> — кинуть чаевые\n• <b>/help</b> — все команды",
        "en": "Try this:\n• <b>/deposit</b> — top up (QR)\n• <b>/bets</b> — back a market\n• <b>/tip 1 @nick</b> — send a tip\n• <b>/help</b> — all commands",
        "zh": "试试这些：\n• <b>/deposit</b> —— 充值（二维码）\n• <b>/bets</b> —— 参与投注\n• <b>/tip 1 @昵称</b> —— 发送打赏\n• <b>/help</b> —— 全部命令",
    },
    "start_footer": {
        "ru": "🏗️ Работает на <b>Base</b> — дешёвой L2 от Coinbase · base.org\n🧑‍💻 Автор: @b2wmain · @ssrjkk · x.com/ludych1 · github.com/ssrjkk",
        "en": "🏗️ Built on <b>Base</b> — Coinbase's low-cost L2 · base.org\n🧑‍💻 Team: @b2wmain · @ssrjkk · x.com/ludych1 · github.com/ssrjkk",
        "zh": "🏗️ 基于 <b>Base</b> —— Coinbase 推出的低成本 L2 · base.org\n🧑‍💻 团队：@b2wmain · @ssrjkk · x.com/ludych1 · github.com/ssrjkk",
    },
    # ----- hints (menu buttons) -----
    "hint_tip": {
        "ru": "💸 <b>Чаевые</b>\n\n/tip 5 @ник — кинуть 5 USDC\n/tip 5 ответом на сообщение — автору.\n\nВ группах работают реакции-чаевые ⚡ — ставь 🔥/❤️/⚡ на сообщение, и автор получает USDC (вкл/выкл: /settings).",
        "en": "💸 <b>Tips</b>\n\n/tip 5 @nick — send 5 USDC\n/tip 5 as a reply — tips the author.\n\nIn groups, reaction tips ⚡ work too: react 🔥/❤️/⚡ to a message and its author gets USDC (toggle: /settings).",
        "zh": "💸 <b>打赏</b>\n\n/tip 5 @昵称 —— 发送 5 USDC\n/tip 5 回复消息 —— 打赏作者。\n\n群聊中支持表情打赏 ⚡：给消息添加 🔥/❤️/⚡，作者即可获得 USDC（开关见 /settings）。",
    },
    "hint_ask": {
        "ru": "🧠 <b>ИИ-ассистент</b>\n\n/ask &lt;вопрос&gt; — например: /ask Что такое Base?\nМожно задать вопрос ответом на сообщение — ИИ увидит его контекст.",
        "en": "🧠 <b>AI assistant</b>\n\n/ask &lt;question&gt; — e.g. /ask What is Base?\nReply to any message with your question and the AI will see its context.",
        "zh": "🧠 <b>AI 助手</b>\n\n/ask &lt;问题&gt; —— 例如：/ask 什么是 Base？\n也可以回复某条消息提问，AI 会看到该消息的上下文。",
    },
    "hint_wallet": {
        "ru": "👛 <b>Кошелёк</b>\n\n/wallet — адрес и ключи\n/deposit — пополнить · /withdraw &lt;адрес&gt; &lt;сумма&gt; — вывести\n/link &lt;адрес&gt; — привязать внешний кошелёк (авто-зачисление)\n/import — вход по сид-фразе · /export — выгрузка ключа",
        "en": "👛 <b>Wallet</b>\n\n/wallet — address and keys\n/deposit — top up · /withdraw &lt;address&gt; &lt;amount&gt; — withdraw\n/link &lt;address&gt; — link an external wallet (auto-credits)\n/import — sign in with a seed phrase · /export — export keys",
        "zh": "👛 <b>钱包</b>\n\n/wallet —— 地址与密钥\n/deposit —— 充值 · /withdraw &lt;地址&gt; &lt;金额&gt; —— 提现\n/link &lt;地址&gt; —— 绑定外部钱包（自动到账）\n/import —— 用助记词登录 · /export —— 导出密钥",
    },
    # ----- balance -----
    "bal_linked": {
        "ru": "\n🔗 Кошелёк: <code>{addr}</code>",
        "en": "\n🔗 Wallet: <code>{addr}</code>",
        "zh": "\n🔗 钱包：<code>{addr}</code>",
    },
    "bal_nolink": {
        "ru": "\n🔗 Кошелёк не привязан — /link",
        "en": "\n🔗 No wallet linked — /link",
        "zh": "\n🔗 尚未绑定钱包 —— /link",
    },
    "bal_ingame": {
        "ru": "\n🎲 В игре: <b>{n}</b> позиция(и) на <b>{stake} USDC</b>\n🏆 Потенциальный выигрыш: <b>{pot} USDC</b>\n📌 Твои ставки: /mybets",
        "en": "\n🎲 In play: <b>{n}</b> position(s) worth <b>{stake} USDC</b>\n🏆 Potential win: <b>{pot} USDC</b>\n📌 Your bets: /mybets",
        "zh": "\n🎲 进行中：<b>{n}</b> 个仓位，共 <b>{stake} USDC</b>\n🏆 潜在赢额：<b>{pot} USDC</b>\n📌 我的投注：/mybets",
    },
    "bal_fees": {
        "ru": "\n🧾 Заработано на рынках: <b>{fees} USDC</b>",
        "en": "\n🧾 Earned on markets: <b>{fees} USDC</b>",
        "zh": "\n🧾 市场收益：<b>{fees} USDC</b>",
    },
    # ----- deposit -----
    "dep_head": {
        "ru": "💳 Отправь USDC на адрес бота\n🟦 <b>Сеть Base</b> · монета USDC (ERC-20)",
        "en": "💳 Send USDC to the bot's address\n🟦 <b>Base network</b> · USDC coin (ERC-20)",
        "zh": "💳 将 USDC 转入机器人地址\n🟦 <b>Base 网络</b> · USDC 代币（ERC-20）",
    },
    "dep_linked": {
        "ru": "С твоего привязанного кошелька <code>{addr}</code> — зачислится автоматически ✅",
        "en": "From your linked wallet <code>{addr}</code> deposits credit automatically ✅",
        "zh": "从你绑定的钱包 <code>{addr}</code> 转账将自动到账 ✅",
    },
    "dep_claim": {
        "ru": "После отправки пришли /claim <i>&lt;tx_hash&gt;</i>.\n<b>Удобнее:</b> привяжи кошелёк — /link, и депозиты будут зачисляться сами.",
        "en": "After sending, run /claim <i>&lt;tx_hash&gt;</i>.\n<b>Easier:</b> link your wallet with /link and deposits credit automatically.",
        "zh": "转账后发送 /claim <i>&lt;交易哈希&gt;</i>。\n<b>更方便：</b>用 /link 绑定钱包，充值将自动到账。",
    },
    "dep_public": {
        "ru": "🏗️ Операция в блокчейне, видна всем: basescan.org",
        "en": "🏗️ On-chain and visible to everyone: basescan.org",
        "zh": "🏗️ 链上操作，人人可见：basescan.org",
    },
    "dep_disclaimer": {
        "ru": "⚠️ <b>Дисклеймер:</b> средства хранит бот (кастодиальный кошелёк). Свой ключ и сид-фразу можно забрать в любой момент: /wallet export",
        "en": "⚠️ <b>Disclaimer:</b> funds are held by the bot (custodial wallet). You can export your key and seed anytime: /wallet export",
        "zh": "⚠️ <b>免责声明：</b>资金由机器人托管。你可以随时导出密钥和助记词：/wallet export",
    },
    # ----- donate -----
    "donate_text": {
        "ru": "💛 <b>Твоя страница донатов</b>\n\nСкинь эту ссылку куда угодно — по ней откроется твой адрес для USDC:\n<code>{link}</code>\n\nПо ссылке сразу видно, кому и куда платить — без посредников.",
        "en": "💛 <b>Your donate page</b>\n\nShare this link anywhere — it opens your USDC address:\n<code>{link}</code>\n\nIt shows exactly who gets paid and where — no middlemen.",
        "zh": "💛 <b>你的捐赠页</b>\n\n把这条链接分享到任何地方——打开就是你的 USDC 收款地址：\n<code>{link}</code>\n\n谁收款、付到哪里一目了然——没有中间商。",
    },
    # ----- help (full command reference) -----
    "help_full": {
        "ru": (
            "🤖 <b>Tippy</b> — экономика сообщества в USDC на <b>Base</b>.\n"
            "🟦 Сеть Base · монета USDC · все переводы в блокчейне\n\n"
            "💸 <b>Чаевые</b>\n"
            "• /tip 5 @nick — кинуть 5 USDC\n"
            "• /tip 5 (ответом на сообщение) — кинуть автору\n"
            "• 🔥/❤️/⚡/👏/🎉 на сообщение — реакция-чаевые (в группах)\n"
            "• /rain 10 — разбросать 10 USDC случайным участникам группы 🌧️\n\n"
            "📈 <b>Рынки предсказаний (AMM)</b>\n"
            "• /market create &lt;банк&gt; &lt;вопрос&gt; | &lt;в1&gt; | &lt;в2&gt; [24h] — создать рынок с живыми котировками\n"
            "• /markets — открытые рынки (кнопки)\n"
            "• /trade &lt;id&gt; &lt;номер&gt; &lt;сумма&gt; — купить доли по живой цене\n"
            "• /sell &lt;id&gt; &lt;номер&gt; [50%] — продать доли в любой момент\n"
            "• /positions — твои позиции и PnL\n\n"
            "🎲 <b>Ставки (пулы)</b>\n"
            "• /bet create &lt;вопрос&gt; | &lt;в1&gt; | &lt;в2&gt; [24h] — создать\n"
            "• /bets — открытые ставки (кнопки)\n"
            "• /bet &lt;id&gt; &lt;номер&gt; &lt;сумма&gt; — поставить\n"
            "• /mybets — твои позиции\n"
            "• /resolve &lt;id&gt; &lt;номер&gt; — закрыть (создатель)\n"
            "• /cancel &lt;id&gt; — отменить / вернуть деньги после истечения\n\n"
            "🧠 <b>ИИ-ассистент</b>\n"
            "• /ask &lt;вопрос&gt; — спросить ИИ о чём угодно (можно ответом на сообщение)\n\n"
            "💰 <b>Кошелёк</b>\n"
            "• /donate — твоя страница донатов с QR\n"
            "• /deposit — QR + адрес для пополнения\n"
            "• /link &lt;адрес&gt; — привязать кошелёк (авто-зачисление)\n"
            "• /withdraw &lt;адрес&gt; &lt;сумма&gt; — вывод (комиссия 1%, мин. 1 USDC)\n\n"
            "📊 <b>Ещё</b>\n"
            "• /menu — меню · /balance · /stats · /top · /history\n"
            "• /settings — уведомления и реакции ⚙️\n\n"
            "🔐 <b>Платный контент</b>\n"
            "• /paywall create 5 Заголовок — создать платный пост\n"
            "• /paywall list · /paywall buy &lt;id&gt; — купить и открыть\n"
            "• /paywall subscribe @канал — доступ к платному каналу\n\n"
            "🟦 <b>На Base</b> · 🪙 USDC (ERC-20) · 🔍 все транзакции в блокчейне\n"
            "🏗️ <b>Base</b> — безопасная, дешёвая, развивающаяся L2 от Coinbase: base.org\n"
            "👛 Свой кошелёк: /wallet · выгрузить ключ и сид: /wallet export · импорт по сид-фразе: /import"
        ),
        "en": (
            "🤖 <b>Tippy</b> — a community economy in USDC on <b>Base</b>.\n"
            "🟦 Base network · USDC coin · every transfer is on-chain\n\n"
            "💸 <b>Tips</b>\n"
            "• /tip 5 @nick — send 5 USDC\n"
            "• /tip 5 (reply to a message) — tip the author\n"
            "• 🔥/❤️/⚡/👏/🎉 on a message — reaction tip (in groups)\n"
            "• /rain 10 — spread 10 USDC among random group members 🌧️\n\n"
            "📈 <b>Prediction markets (AMM)</b>\n"
            "• /market create &lt;bank&gt; &lt;question&gt; | &lt;o1&gt; | &lt;o2&gt; [24h] — create a market with live odds\n"
            "• /markets — open markets (buttons)\n"
            "• /trade &lt;id&gt; &lt;option&gt; &lt;amount&gt; — buy shares at the live price\n"
            "• /sell &lt;id&gt; &lt;option&gt; [50%] — sell shares anytime\n"
            "• /positions — your positions and PnL\n\n"
            "🎲 <b>Bets (pools)</b>\n"
            "• /bet create &lt;question&gt; | &lt;o1&gt; | &lt;o2&gt; [24h] — create\n"
            "• /bets — open bets (buttons)\n"
            "• /bet &lt;id&gt; &lt;option&gt; &lt;amount&gt; — place a bet\n"
            "• /mybets — your positions\n"
            "• /resolve &lt;id&gt; &lt;option&gt; — close it (creator)\n"
            "• /cancel &lt;id&gt; — cancel / refund after expiry\n\n"
            "🧠 <b>AI assistant</b>\n"
            "• /ask &lt;question&gt; — ask the AI anything (or reply to a message)\n\n"
            "💰 <b>Wallet</b>\n"
            "• /donate — your donate page with QR\n"
            "• /deposit — QR + address to top up\n"
            "• /link &lt;address&gt; — link a wallet (auto-credits)\n"
            "• /withdraw &lt;address&gt; &lt;amount&gt; — withdraw (1% fee, min 1 USDC)\n\n"
            "📊 <b>More</b>\n"
            "• /menu — menu · /balance · /stats · /top · /history\n"
            "• /settings — notifications and reactions ⚙️\n\n"
            "🔐 <b>Paid content</b>\n"
            "• /paywall create 5 Title — create a paid post\n"
            "• /paywall list · /paywall buy &lt;id&gt; — buy and unlock\n"
            "• /paywall subscribe @channel — access to a paid channel\n\n"
            "🟦 <b>On Base</b> · 🪙 USDC (ERC-20) · 🔍 all transactions on-chain\n"
            "🏗️ <b>Base</b> — safe, cheap, growing L2 by Coinbase: base.org\n"
            "👛 Your wallet: /wallet · export key & seed: /wallet export · import seed: /import"
        ),
        "zh": (
            "🤖 <b>Tippy</b> —— 建立在 <b>Base</b> 上的 USDC 社区经济。\n"
            "🟦 Base 网络 · USDC 代币 · 所有转账都在链上\n\n"
            "💸 <b>打赏</b>\n"
            "• /tip 5 @昵称 —— 发送 5 USDC\n"
            "• /tip 5（回复某条消息）—— 打赏作者\n"
            "• 给消息加 🔥/❤️/⚡/👏/🎉 —— 表情打赏（群聊中）\n"
            "• /rain 10 —— 把 10 USDC 随机分给群成员 🌧️\n\n"
            "📈 <b>预测市场（AMM）</b>\n"
            "• /market create &lt;资金&gt; &lt;问题&gt; | &lt;选项1&gt; | &lt;选项2&gt; [24h] —— 创建实时报价市场\n"
            "• /markets —— 开放市场（按钮）\n"
            "• /trade &lt;id&gt; &lt;选项&gt; &lt;金额&gt; —— 按实时价格买入份额\n"
            "• /sell &lt;id&gt; &lt;选项&gt; [50%] —— 随时卖出份额\n"
            "• /positions —— 你的持仓与盈亏\n\n"
            "🎲 <b>投注（奖池）</b>\n"
            "• /bet create &lt;问题&gt; | &lt;选项1&gt; | &lt;选项2&gt; [24h] —— 创建\n"
            "• /bets —— 开放投注（按钮）\n"
            "• /bet &lt;id&gt; &lt;选项&gt; &lt;金额&gt; —— 下注\n"
            "• /mybets —— 我的持仓\n"
            "• /resolve &lt;id&gt; &lt;选项&gt; —— 结算（创建者）\n"
            "• /cancel &lt;id&gt; —— 到期后取消 / 退款\n\n"
            "🧠 <b>AI 助手</b>\n"
            "• /ask &lt;问题&gt; —— 向 AI 提问（也可回复消息提问）\n\n"
            "💰 <b>钱包</b>\n"
            "• /donate —— 带二维码的捐赠页\n"
            "• /deposit —— 充值二维码与地址\n"
            "• /link &lt;地址&gt; —— 绑定钱包（自动到账）\n"
            "• /withdraw &lt;地址&gt; &lt;金额&gt; —— 提现（手续费 1%，最低 1 USDC）\n\n"
            "📊 <b>更多</b>\n"
            "• /menu —— 菜单 · /balance · /stats · /top · /history\n"
            "• /settings —— 通知与表情打赏 ⚙️\n\n"
            "🔐 <b>付费内容</b>\n"
            "• /paywall create 5 标题 —— 创建付费帖子\n"
            "• /paywall list · /paywall buy &lt;id&gt; —— 购买并解锁\n"
            "• /paywall subscribe @频道 —— 访问付费频道\n\n"
            "🟦 <b>基于 Base</b> · 🪙 USDC（ERC-20）· 🔍 所有交易上链\n"
            "🏗️ <b>Base</b> —— Coinbase 推出、安全且廉价的 L2：base.org\n"
            "👛 你的钱包：/wallet · 导出密钥与助记词：/wallet export · 用助记词导入：/import"
        ),
    },
}


def norm(lang: str | None) -> str:
    return lang if lang in LANGS else DEFAULT_LANG


def t(lang: str | None, key: str, **kwargs) -> str:
    table = STRINGS[key]
    return table[norm(lang)].format(**kwargs)
