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
    "btn_mini_app": {
        "ru": "📱 Mini App",
        "en": "📱 Mini App",
        "zh": "📱 Mini App",
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
    # ----- tip -----
    "tip_ok": {
        "ru": "💸 Ты отправил <b>{amount} USDC</b> пользователю @{to}!",
        "en": "💸 You sent <b>{amount} USDC</b> to @{to}!",
        "zh": "💸 你向 @{to} 发送了 <b>{amount} USDC</b>！",
    },
    "tip_fail_balance": {
        "ru": "❌ Недостаточно средств. Баланс: <b>{bal} USDC</b>",
        "en": "❌ Insufficient funds. Balance: <b>{bal} USDC</b>",
        "zh": "❌ 余额不足。余额：<b>{bal} USDC</b>",
    },
    "tip_fail_self": {
        "ru": "❌ Нельзя отправить чаевые самому себе.",
        "en": "❌ You can't tip yourself.",
        "zh": "❌ 不能给自己打赏。",
    },
    "tip_fail_user": {
        "ru": "❌ Пользователь @{user} не найден. Он должен сначала написать боту.",
        "en": "❌ User @{user} not found. They need to message the bot first.",
        "zh": "❌ 用户 @{user} 未找到。他需要先给机器人发消息。",
    },
    "tip_need_amount": {
        "ru": "💸 Формат: /tip &lt;сумма&gt; @ник\nПример: /tip 5 @friend",
        "en": "💸 Format: /tip &lt;amount&gt; @nick\nExample: /tip 5 @friend",
        "zh": "💸 格式：/tip &lt;金额&gt; @昵称\n示例：/tip 5 @friend",
    },
    "tip_reply": {
        "ru": "💸 Сколько USDC отправить автору сообщения?",
        "en": "💸 How much USDC to send to the message author?",
        "zh": "💸 向消息作者发送多少 USDC？",
    },
    # ----- markets -----
    "market_created": {
        "ru": "✅ Рынок создан!\n<b>{question}</b>\nID: <code>{id}</code> · Банк: {bank} USDC",
        "en": "✅ Market created!\n<b>{question}</b>\nID: <code>{id}</code> · Bank: {bank} USDC",
        "zh": "✅ 市场已创建！\n<b>{question}</b>\nID: <code>{id}</code> · 资金池：{bank} USDC",
    },
    "market_buy_ok": {
        "ru": "📈 Куплено! {n} долей «{label}» за {amount} USDC.\nНовая цена: {price}%",
        "en": "📈 Bought! {n} shares of \"{label}\" for {amount} USDC.\nNew price: {price}%",
        "zh": "📈 已买入！{n} 份「{label}」，花费 {amount} USDC。\n新价格：{price}%",
    },
    "market_sell_ok": {
        "ru": "📉 Продано! {n} долей «{label}» за {amount} USDC.",
        "en": "📉 Sold! {n} shares of \"{label}\" for {amount} USDC.",
        "zh": "📉 已卖出！{n} 份「{label}」，获得 {amount} USDC。",
    },
    "market_positions": {
        "ru": "📊 <b>Твои позиции</b>\n\n{lines}\n\n💰 Баланс: <b>{bal} USDC</b>",
        "en": "📊 <b>Your positions</b>\n\n{lines}\n\n💰 Balance: <b>{bal} USDC</b>",
        "zh": "📊 <b>你的持仓</b>\n\n{lines}\n\n💰 余额：<b>{bal} USDC</b>",
    },
    "market_no_positions": {
        "ru": "📊 У тебя пока нет позиций. Зайди в /markets!",
        "en": "📊 No positions yet. Check out /markets!",
        "zh": "📊 你还没有持仓。去看看 /markets！",
    },
    "market_need_args": {
        "ru": "📈 Формат: /market create &lt;банк&gt; &lt;вопрос&gt; | &lt;в1&gt; | &lt;в2&gt; [24h]\nПример: /market create 50 Кто победит? | Biden | Trump | 24h",
        "en": "📈 Format: /market create &lt;bank&gt; &lt;question&gt; | &lt;o1&gt; | &lt;o2&gt; [24h]\nExample: /market create 50 Who wins? | Biden | Trump | 24h",
        "zh": "📈 格式：/market create &lt;资金&gt; &lt;问题&gt; | &lt;选项1&gt; | &lt;选项2&gt; [24h]\n示例：/market create 50 谁赢？ | Biden | Trump | 24h",
    },
    # ----- bets -----
    "bet_created": {
        "ru": "✅ Ставка создана!\n<b>{question}</b>\nID: <code>{id}</code>",
        "en": "✅ Bet created!\n<b>{question}</b>\nID: <code>{id}</code>",
        "zh": "✅ 投注已创建！\n<b>{question}</b>\nID: <code>{id}</code>",
    },
    "bet_placed": {
        "ru": "🎲 Поставлено {amount} USDC на «{label}»!\nВсего в пуле: {pot} USDC",
        "en": "🎲 Bet {amount} USDC on \"{label}\"!\nTotal pool: {pot} USDC",
        "zh": "🎲 下注 {amount} USDC 到「{label}」！\n奖池总额：{pot} USDC",
    },
    "bet_resolved": {
        "ru": "🏆 Ставка закрыта! Победил вариант «{label}».\nПобедители делят {pot} USDC (минус 2% комиссия).",
        "en": "🏆 Bet resolved! Winner: \"{label}\".\nWinners share {pot} USDC (minus 2% fee).",
        "zh": "🏆 投注已结算！获胜选项：「{label}」。\n赢家瓜分 {pot} USDC（扣除 2% 手续费）。",
    },
    "bet_cancelled": {
        "ru": "↩️ Ставка отменена. Деньги возвращены участникам.",
        "en": "↩️ Bet cancelled. Funds returned to participants.",
        "zh": "↩️ 投注已取消。资金已退还给参与者。",
    },
    "bet_need_args": {
        "ru": "🎲 Формат: /bet create &lt;вопрос&gt; | &lt;в1&gt; | &lt;в2&gt; [24h]\nПример: /bet create Кто первый? | Alice | Bob | 24h",
        "en": "🎲 Format: /bet create &lt;question&gt; | &lt;o1&gt; | &lt;o2&gt; [24h]\nExample: /bet create Who first? | Alice | Bob | 24h",
        "zh": "🎲 格式：/bet create &lt;问题&gt; | &lt;选项1&gt; | &lt;选项2&gt; [24h]\n示例：/bet create 谁先到？ | Alice | Bob | 24h",
    },
    # ----- rain -----
    "rain_ok": {
        "ru": "🌧️ Раздано <b>{amount} USDC</b> среди {count} участников!",
        "en": "🌧️ Spread <b>{amount} USDC</b> among {count} members!",
        "zh": "🌧️ 已将 <b>{amount} USDC</b> 分给 {count} 位成员！",
    },
    "rain_fail": {
        "ru": "❌ Не удалось: {reason}. Баланс: <b>{bal} USDC</b>",
        "en": "❌ Failed: {reason}. Balance: <b>{bal} USDC</b>",
        "zh": "❌ 失败：{reason}。余额：<b>{bal} USDC</b>",
    },
    # ----- withdraw -----
    "withdraw_ok": {
        "ru": "⬆️ Вывод <b>{amount} USDC</b> на <code>{addr}</code> отправлен!\nTX: <code>{tx}</code>",
        "en": "⬆️ Withdrawal of <b>{amount} USDC</b> to <code>{addr}</code> sent!\nTX: <code>{tx}</code>",
        "zh": "⬆️ 提现 <b>{amount} USDC</b> 到 <code>{addr}</code> 已发送！\nTX：<code>{tx}</code>",
    },
    "withdraw_fail_balance": {
        "ru": "❌ Недостаточно средств. Баланс: <b>{bal} USDC</b> (мин. 1 USDC, комиссия 1%)",
        "en": "❌ Insufficient funds. Balance: <b>{bal} USDC</b> (min 1 USDC, 1% fee)",
        "zh": "❌ 余额不足。余额：<b>{bal} USDC</b>（最低 1 USDC，手续费 1%）",
    },
    "withdraw_need_args": {
        "ru": "⬆️ Формат: /withdraw &lt;адрес&gt; &lt;сумма&gt;\nПример: /withdraw 0x123...abc 10",
        "en": "⬆️ Format: /withdraw &lt;address&gt; &lt;amount&gt;\nExample: /withdraw 0x123...abc 10",
        "zh": "⬆️ 格式：/withdraw &lt;地址&gt; &lt;金额&gt;\n示例：/withdraw 0x123...abc 10",
    },
    # ----- link -----
    "link_ok": {
        "ru": "🔗 Кошелёк <code>{addr}</code> привязан! Депозиты будут зачисляться автоматически.",
        "en": "🔗 Wallet <code>{addr}</code> linked! Deposits will credit automatically.",
        "zh": "🔗 钱包 <code>{addr}</code> 已绑定！充值将自动到账。",
    },
    "link_need_address": {
        "ru": "🔗 Формат: /link &lt;адрес Base&gt;\nПример: /link 0x123...abc",
        "en": "🔗 Format: /link &lt;Base address&gt;\nExample: /link 0x123...abc",
        "zh": "🔗 格式：/link &lt;Base 地址&gt;\n示例：/link 0x123...abc",
    },
    # ----- claim -----
    "claim_ok": {
        "ru": "✅ Депозит зачислен: <b>{amount} USDC</b>!\nБаланс: <b>{bal} USDC</b>",
        "en": "✅ Deposit credited: <b>{amount} USDC</b>!\nBalance: <b>{bal} USDC</b>",
        "zh": "✅ 充值已到账：<b>{amount} USDC</b>！\n余额：<b>{bal} USDC</b>",
    },
    "claim_fail": {
        "ru": "❌ Транзакция не найдена или уже зачислена. Попробуй позже.",
        "en": "❌ Transaction not found or already credited. Try again later.",
        "zh": "❌ 交易未找到或已到账。请稍后重试。",
    },
    # ----- stats -----
    "stats_text": {
        "ru": "📊 <b>Статистика Tippy</b>\n\n👥 Пользователей: <b>{users}</b>\n💸 Всего чаевых: <b>{tips} USDC</b>\n📈 Объём ставок: <b>{bets} USDC</b>\n🏦 Общий объём: <b>{volume} USDC</b>\n💰 Комиссии: <b>{fees} USDC</b>",
        "en": "📊 <b>Tippy Stats</b>\n\n👥 Users: <b>{users}</b>\n💸 Total tips: <b>{tips} USDC</b>\n📈 Bets volume: <b>{bets} USDC</b>\n🏦 Total volume: <b>{volume} USDC</b>\n💰 Fees: <b>{fees} USDC</b>",
        "zh": "📊 <b>Tippy 统计</b>\n\n👥 用户数：<b>{users}</b>\n💸 打赏总额：<b>{tips} USDC</b>\n📈 投注总额：<b>{bets} USDC</b>\n🏦 总交易量：<b>{volume} USDC</b>\n💰 手续费：<b>{fees} USDC</b>",
    },
    "top_text": {
        "ru": "🏆 <b>Топ пользователей</b>\n\n{lines}",
        "en": "🏆 <b>Top users</b>\n\n{lines}",
        "zh": "🏆 <b>用户排行榜</b>\n\n{lines}",
    },
    "history_text": {
        "ru": "🧾 <b>История операций</b>\n\n{lines}\n\n<i>Показаны последние {limit} операций</i>",
        "en": "🧾 <b>Transaction history</b>\n\n{lines}\n\n<i>Last {limit} transactions shown</i>",
        "zh": "🧾 <b>交易记录</b>\n\n{lines}\n\n<i>显示最近 {limit} 笔交易</i>",
    },
    "history_empty": {
        "ru": "🧾 История пока пуста. Попробуй /deposit или /tip!",
        "en": "🧾 No history yet. Try /deposit or /tip!",
        "zh": "🧾 暂无记录。试试 /deposit 或 /tip！",
    },
    # ----- rain -----
    "rain_only_groups": {
        "ru": "🌧️ /rain работает только в группах!",
        "en": "🌧️ /rain only works in groups!",
        "zh": "🌧️ /rain 仅在群聊中可用！",
    },
    "rain_format": {
        "ru": "Формат: /rain 10 (или /rain 10 15 — на 15 чел.)",
        "en": "Format: /rain 10 (or /rain 10 15 — for 15 people)",
        "zh": "格式：/rain 10（或 /rain 10 15——分给 15 人）",
    },
    "rain_max": {
        "ru": "Максимум за один дождь: <b>{n} USDC</b>.",
        "en": "Max per rain: <b>{n} USDC</b>.",
        "zh": "单次最多：<b>{n} USDC</b>。",
    },
    "rain_need_positive": {
        "ru": "Сумма должна быть больше нуля.",
        "en": "Amount must be greater than zero.",
        "zh": "金额必须大于零。",
    },
    "rain_max_participants": {
        "ru": "Максимум участников: <b>{n}</b>.",
        "en": "Max participants: <b>{n}</b>.",
        "zh": "最多参与人数：<b>{n}</b>。",
    },
    "rain_recipients": {
        "ru": "🎁 Получили: {names}{tail}\n\n🌧️ Дождь закончился!",
        "en": "🎁 Received: {names}{tail}\n\n🌧️ Rain complete!",
        "zh": "🎁 获得者：{names}{tail}\n\n🌧️ 降雨结束！",
    },
    # ----- tip errors -----
    "tip_need_amount": {
        "ru": "Формат: /tip 5 @nick (или /tip 5 ответом на сообщение)",
        "en": "Format: /tip 5 @nick (or /tip 5 as a reply)",
        "zh": "格式：/tip 5 @昵称（或回复消息后写 /tip 5）",
    },
    "tip_max": {
        "ru": "Максимум чаевых за раз: <b>{n} USDC</b>.",
        "en": "Max tip per send: <b>{n} USDC</b>.",
        "zh": "单次最多：<b>{n} USDC</b>。",
    },
    "tip_need_recipient": {
        "ru": "Укажи получателя: /tip 5 @username",
        "en": "Specify recipient: /tip 5 @username",
        "zh": "指定接收者：/tip 5 @username",
    },
    "tip_user_not_found": {
        "ru": "Не нашёл @{user}. Пусть напишет боту в ЛС (/start).",
        "en": "Can't find @{user}. They need to message the bot first (/start).",
        "zh": "找不到 @{user}。他需要先给机器人发消息（/start）。",
    },
    "tip_who": {
        "ru": "Кому кидаем? /tip 5 @nick — или ответь на сообщение и напиши /tip 5",
        "en": "Who to tip? /tip 5 @nick — or reply to a message and write /tip 5",
        "zh": "打赏谁？/tip 5 @昵称——或回复消息后写 /tip 5",
    },
    "tip_self": {
        "ru": "Себе кидать нельзя 😅",
        "en": "You can't tip yourself 😅",
        "zh": "不能给自己打赏 😅",
    },
    "tip_no_balance": {
        "ru": "❌ Недостаточно баланса. Пополни: /deposit",
        "en": "❌ Insufficient balance. Top up: /deposit",
        "zh": "❌ 余额不足。充值：/deposit",
    },
    "tip_sent": {
        "ru": "💸 <b>{sender}</b> → {mention}\n<b>{amount} USDC</b>\nОстаток: {bal} USDC",
        "en": "💸 <b>{sender}</b> → {mention}\n<b>{amount} USDC</b>\nBalance: {bal} USDC",
        "zh": "💸 <b>{sender}</b> → {mention}\n<b>{amount} USDC</b>\n余额：{bal} USDC",
    },
    "tip_received": {
        "ru": "💸 <b>Тебе кинули {amount} USDC</b>\nОт: @{sender}\n\nБаланс: /balance",
        "en": "💸 <b>You received {amount} USDC</b>\nFrom: @{sender}\n\nBalance: /balance",
        "zh": "💸 <b>你收到了 {amount} USDC</b>\n来自：@{sender}\n\n余额：/balance",
    },
    # ----- market buy/sell -----
    "market_buy_card": {
        "ru": "🛒 #{mid}: <b>{label}</b>\n\nСколько вкладываем? Доли начисляются по живой цене.",
        "en": "🛒 #{mid}: <b>{label}</b>\n\nHow much to invest? Shares are priced live.",
        "zh": "🛒 #{mid}: <b>{label}</b>\n\n投入多少？份额按实时价格计算。",
    },
    "market_buy_ok_detail": {
        "ru": "✅ Куплено!\n📈 #{mid} — <b>{label}</b>\nДоли: <b>{shares}</b> по цене {price}\nПотрачено: <b>{cost} USDC</b>\nОстаток: {bal} USDC",
        "en": "✅ Bought!\n📈 #{mid} — <b>{label}</b>\nShares: <b>{shares}</b> at {price}\nSpent: <b>{cost} USDC</b>\nBalance: {bal} USDC",
        "zh": "✅ 已买入！\n📈 #{mid} — <b>{label}</b>\n份额：<b>{shares}</b>，价格 {price}\n花费：<b>{cost} USDC</b>\n余额：{bal} USDC",
    },
    "market_sell_card": {
        "ru": "📉 #{mid}: продаём доли?\nУ тебя: <b>{held}</b> долей по живой цене.",
        "en": "📉 #{mid}: sell shares?\nYou hold: <b>{held}</b> shares at live price.",
        "zh": "📉 #{mid}：卖出份额？\n你持有：<b>{held}</b> 份，按实时价格。",
    },
    "market_sell_ok_detail": {
        "ru": "✅ Продано!\n📉 #{mid} — <b>{label}</b>\nДоли: <b>{shares}</b> по цене {price}\nПолучено: <b>{value} USDC</b>",
        "en": "✅ Sold!\n📉 #{mid} — <b>{label}</b>\nShares: <b>{shares}</b> at {price}\nReceived: <b>{value} USDC</b>",
        "zh": "✅ 已卖出！\n📉 #{mid} — <b>{label}</b>\n份额：<b>{shares}</b>，价格 {price}\n获得：<b>{value} USDC</b>",
    },
    "market_open": {
        "ru": "📈 Открытых рынков нет.\nСоздай первый: /market create <банк> <вопрос> | <в1> | <в2>",
        "en": "📈 No open markets.\nCreate the first: /market create <bank> <question> | <o1> | <o2>",
        "zh": "📈 暂无开放市场。\n创建第一个：/market create <资金> <问题> | <选项1> | <选项2>",
    },
    "market_list_header": {
        "ru": "📈 <b>Рынки предсказаний</b> — живые котировки",
        "en": "📈 <b>Prediction markets</b> — live odds",
        "zh": "📈 <b>预测市场</b> — 实时行情",
    },
    "market_list_hint": {
        "ru": "Нажми на рынок — карточка с котировками и кнопками.",
        "en": "Tap a market for odds and buttons.",
        "zh": "点击市场查看行情和按钮。",
    },
    "market_not_found": {
        "ru": "Рынок не найден",
        "en": "Market not found",
        "zh": "市场未找到",
    },
    "market_closed": {
        "ru": "Рынок уже закрыт",
        "en": "Market already closed",
        "zh": "市场已关闭",
    },
    "market_only_creator": {
        "ru": "Закрыть может только создатель рынка",
        "en": "Only the creator can close the market",
        "zh": "只有创建者才能关闭市场",
    },
    "market_resolve_title": {
        "ru": "🏁 <b>Закрыть рынок #{mid}</b> — «{question}»\n\nКто победил? Победные доли платят 1 USDC за долю, остаток пула — тебе.",
        "en": "🏁 <b>Close market #{mid}</b> — \"{question}\"\n\nWho won? Winning shares pay 1 USDC each, pool remainder goes to you.",
        "zh": "🏁 <b>关闭市场 #{mid}</b> ——「{question}」\n\n谁赢了？获胜份额每份支付 1 USDC，剩余资金归你。",
    },
    "market_balance": {
        "ru": "❌ Недостаточно баланса. Пополни: /deposit",
        "en": "❌ Insufficient balance. Top up: /deposit",
        "zh": "❌ 余额不足。充值：/deposit",
    },
    "market_positions_empty": {
        "ru": "📈 У тебя нет открытых позиций. Рынки: /markets",
        "en": "📈 No open positions. Markets: /markets",
        "zh": "📈 没有持仓。市场：/markets",
    },
    "market_trade_format": {
        "ru": "Формат: /trade <id> <номер> <сумма>",
        "en": "Format: /trade <id> <option> <amount>",
        "zh": "格式：/trade <id> <选项> <金额>",
    },
    "market_trade_max": {
        "ru": "Максимум за одну сделку: <b>{n} USDC</b>.",
        "en": "Max per trade: <b>{n} USDC</b>.",
        "zh": "单笔最大：<b>{n} USDC</b>。",
    },
    "market_trade_closed": {
        "ru": "Рынок не найден или уже закрыт.",
        "en": "Market not found or already closed.",
        "zh": "市场未找到或已关闭。",
    },
    "market_trade_deadline": {
        "ru": "⏰ Дедлайн рынка прошёл.",
        "en": "⏰ Market deadline has passed.",
        "zh": "⏰ 市场截止时间已过。",
    },
    "market_trade_badopt": {
        "ru": "Неверный номер варианта.",
        "en": "Invalid option number.",
        "zh": "无效选项编号。",
    },
    "market_trade_toosmall": {
        "ru": "Слишком маленькая сумма — доли не начисляются. Увеличь.",
        "en": "Amount too small — no shares issued. Increase it.",
        "zh": "金额太小——不发放份额。请增加金额。",
    },
    "market_sell_format": {
        "ru": "Формат: /sell <id> <номер> [процент%]",
        "en": "Format: /sell <id> <option> [percent%]",
        "zh": "格式：/sell <id> <选项> [百分比%]",
    },
    "market_sell_no_shares": {
        "ru": "У тебя нет долей этого исхода. Позиции: /positions",
        "en": "You don't hold shares of this outcome. Positions: /positions",
        "zh": "你没有持有此选项的份额。持仓：/positions",
    },
    "market_sell_done": {
        "ru": "✅ Продано!\n📉 #{mid} — <b>{label}</b>\nДоли: <b>{shares}</b> по цене {price}\nПолучено: <b>{value} USDC</b>",
        "en": "✅ Sold!\n📉 #{mid} — <b>{label}</b>\nShares: <b>{shares}</b> at {price}\nReceived: <b>{value} USDC</b>",
        "zh": "✅ 已卖出！\n📉 #{mid} — <b>{label}</b>\n份额：<b>{shares}</b>，价格 {price}\n获得：<b>{value} USDC</b>",
    },
    "market_win": {
        "ru": "🏆 Ты выиграл {amount} USDC!\n📈 #{mid} — «{question}»\nПобедил: <b>{winner}</b>\nБаланс: /balance",
        "en": "🏆 You won {amount} USDC!\n📈 #{mid} — \"{question}\"\nWinner: <b>{winner}</b>\nBalance: /balance",
        "zh": "🏆 你赢了 {amount} USDC！\n📈 #{mid} ——「{question}」\n获胜者：<b>{winner}</b>\n余额：/balance",
    },
    "market_lose": {
        "ru": "📈 Рынок #{mid} — «{question}» закрыт.\nПобедил: <b>{winner}</b>\nТвои доли не сыграли. Новые рынки: /markets",
        "en": "📈 Market #{mid} — \"{question}\" closed.\nWinner: <b>{winner}</b>\nYour shares didn't win. New markets: /markets",
        "zh": "📈 市场 #{mid} ——「{question}」已关闭。\n获胜者：<b>{winner}</b>\n你的份额未中奖。更多市场：/markets",
    },
    # ----- bet errors -----
    "bet_not_found": {
        "ru": "Ставка не найдена.",
        "en": "Bet not found.",
        "zh": "投注未找到。",
    },
    "bet_closed": {
        "ru": "Эта ставка уже закрыта.",
        "en": "This bet is already closed.",
        "zh": "此投注已关闭。",
    },
    "bet_deadline_passed": {
        "ru": "⏰ Время приёма ставок истекло. Жди решения создателя: /bets",
        "en": "⏰ Betting period ended. Wait for the creator to resolve: /bets",
        "zh": "⏰ 投注时间已结束。等待创建者结算：/bets",
    },
    "bet_invalid_option": {
        "ru": "Неверный номер варианта.",
        "en": "Invalid option number.",
        "zh": "无效选项编号。",
    },
    "bet_format": {
        "ru": "Формат: /bet <id> <номер> <сумма>",
        "en": "Format: /bet <id> <option> <amount>",
        "zh": "格式：/bet <id> <选项> <金额>",
    },
    "bet_max": {
        "ru": "Максимум за одну ставку: <b>{n} USDC</b>.",
        "en": "Max per bet: <b>{n} USDC</b>.",
        "zh": "单笔最大：<b>{n} USDC</b>。",
    },
    "bet_max_amount": {
        "ru": "Максимум за ставку: <b>{n} USDC</b>.",
        "en": "Max per bet: <b>{n} USDC</b>.",
        "zh": "单笔最大：<b>{n} USDC</b>。",
    },
    "bet_open_empty": {
        "ru": "🎲 Открытых ставок нет.\nСоздай: /bet create <вопрос> | <в1> | <в2>",
        "en": "🎲 No open bets.\nCreate: /bet create <question> | <o1> | <o2>",
        "zh": "🎲 暂无开放投注。\n创建：/bet create <问题> | <选项1> | <选项2>",
    },
    "bet_list_header": {
        "ru": "🎲 <b>Открытые ставки</b>",
        "en": "🎲 <b>Open bets</b>",
        "zh": "🎲 <b>开放投注</b>",
    },
    "bet_list_hint": {
        "ru": "Нажми на рынок — карточка со ставками.",
        "en": "Tap a market for bet details.",
        "zh": "点击市场查看详情。",
    },
    "bet_create_card": {
        "ru": "🎯 #{id} <b>{question}</b>\n✅ <b>Решён:</b> {winner}",
        "en": "🎯 #{id} <b>{question}</b>\n✅ <b>Resolved:</b> {winner}",
        "zh": "🎯 #{id} <b>{question}</b>\n✅ <b>已结算：</b> {winner}",
    },
    "bet_expired": {
        "ru": "🕳️ <b>Рынок истёк</b> — деньги можно вернуть: /cancel <id>",
        "en": "🕳️ <b>Market expired</b> — you can refund: /cancel <id>",
        "zh": "🕳️ <b>市场已过期</b> —— 可以退款：/cancel <id>",
    },
    "bet_open": {
        "ru": "⏰ {deadline} · создатель: @{creator}",
        "en": "⏰ {deadline} · creator: @{creator}",
        "zh": "⏰ {deadline} · 创建者：@{creator}",
    },
    "bet_open_nodl": {
        "ru": "⌛ Закрытие: создателем /resolve · @{creator}",
        "en": "⌛ Closing: by creator /resolve · @{creator}",
        "zh": "⌛ 关闭方式：创建者 /resolve · @{creator}",
    },
    "bet_pot": {
        "ru": "Пул итого: <b>{pot} USDC</b> · участников: {backers}",
        "en": "Total pool: <b>{pot} USDC</b> · backers: {backers}",
        "zh": "奖池总额：<b>{pot} USDC</b> · 参与者：{backers}",
    },
    "bet_fee_note": {
        "ru": "Комиссия на выигрыш: 2% (создателю рынка)",
        "en": "Win fee: 2% (to market creator)",
        "zh": "赢利手续费：2%（归创建者）",
    },
    "bet_resolve_title": {
        "ru": "🏁 <b>Закрыть рынок #{mid}</b> — «{question}»\n\nКто победил?\nПобедители делят пул ({pot} USDC), создатель получает 2% комиссии.",
        "en": "🏁 <b>Close market #{mid}</b> — \"{question}\"\n\nWho won?\nWinners share pool ({pot} USDC), creator gets 2% fee.",
        "zh": "🏁 <b>关闭市场 #{mid}</b> ——「{question}」\n\n谁赢了？\n赢家瓜分奖池（{pot} USDC），创建者获得 2% 手续费。",
    },
    "bet_resolve_win": {
        "ru": "🏆 <b>Ты выиграл {amount} USDC!</b>\n🎲 #{mid} — «{question}»\nПобедил: <b>{winner}</b>\nБаланс: /balance",
        "en": "🏆 <b>You won {amount} USDC!</b>\n🎲 #{mid} — \"{question}\"\nWinner: <b>{winner}</b>\nBalance: /balance",
        "zh": "🏆 <b>你赢了 {amount} USDC！</b>\n🎲 #{mid} ——「{question}」\n获胜者：<b>{winner}</b>\n余额：/balance",
    },
    "bet_resolve_lose": {
        "ru": "🎲 Ставка #{mid} — «{question}» закрыта.\nПобедил: <b>{winner}</b>\nТвои ставки не сыграли. Попробуй: /bets",
        "en": "🎲 Bet #{mid} — \"{question}\" closed.\nWinner: <b>{winner}</b>\nYour bets didn't win. Try: /bets",
        "zh": "🎲 投注 #{mid} ——「{question}」已结算。\n获胜者：<b>{winner}</b>\n你的投注未中奖。试试：/bets",
    },
    "bet_cancel_msg": {
        "ru": "↩️ Ставка #{id} — «{question}» отменена.\nДеньги возвращены: /history",
        "en": "↩️ Bet #{id} — \"{question}\" cancelled.\nFunds returned: /history",
        "zh": "↩️ 投注 #{id} ——「{question}」已取消。\n资金已退还：/history",
    },
    "bet_my_empty": {
        "ru": "🎲 У тебя нет открытых позиций. Ставят: /bets",
        "en": "🎲 No open positions. Bets: /bets",
        "zh": "🎲 没有持仓。投注：/bets",
    },
    "bet_my_header": {
        "ru": "📌 <b>Твои открытые позиции</b>",
        "en": "📌 <b>Your open positions</b>",
        "zh": "📌 <b>你的持仓</b>",
    },
    "bet_confirmed": {
        "ru": "✅ Ставка принята!\n🎯 #{id} — <b>{label}</b> на <b>{amount} USDC</b>\nОстаток: {bal} USDC",
        "en": "✅ Bet accepted!\n🎯 #{id} — <b>{label}</b> for <b>{amount} USDC</b>\nBalance: {bal} USDC",
        "zh": "✅ 投注已接受！\n🎯 #{id} —— <b>{label}</b> 下注 <b>{amount} USDC</b>\n余额：{bal} USDC",
    },
    # ----- wallet extra -----
    "wallet_help": {
        "ru": "Формат:\n• /wallet — адрес\n• /wallet export — ключ и сид\n• /wallet export hot — ключ горячего кошелька (админ)",
        "en": "Format:\n• /wallet — address\n• /wallet export — key and seed\n• /wallet export hot — hot wallet key (admin)",
        "zh": "格式：\n• /wallet —— 地址\n• /wallet export —— 密钥与助记词\n• /wallet export hot —— 热钱包密钥（管理员）",
    },
"wallet_key_export": {
        "ru": "🔑 <b>Твой кошелёк</b>\n\nАдрес: <code>{addr}</code>\nПриватный ключ: <code>{privkey}</code>\nСид-фраза: <code>{seed}</code>\n\n⚠️ Не показывай это никому.",
        "en": "🔑 <b>Your wallet</b>\n\nAddress: <code>{addr}</code>\nPrivate key: <code>{privkey}</code>\nSeed phrase: <code>{seed}</code>\n\n⚠️ Do not share this with anyone.",
        "zh": "🔑 <b>你的钱包</b>\n\n地址：<code>{addr}</code>\n私钥：<code>{privkey}</code>\n助记词：<code>{seed}</code>\n\n⚠️ 请勿向任何人展示。",
    },
    "wallet_addr": {
        "ru": "👛 <b>Твой кошелёк</b>\n\nАдрес: <code>{addr}</code>\n🟦 Сеть Base · монета USDC\n\nКлюч и сид: /wallet export",
        "en": "👛 <b>Your wallet</b>\n\nAddress: <code>{addr}</code>\n🟦 Base network · USDC coin\n\nKey & seed: /wallet export",
        "zh": "👛 <b>你的钱包</b>\n\n地址：<code>{addr}</code>\n🟦 Base 网络 · USDC 代币\n\n密钥与助记词：/wallet export",
    },
    "link_sign_prompt": {
        "ru": "🔗 <b>Привязка кошелька</b>\n\nПодпиши сообщение в кошельке:\n<code>{text}</code>\n\nПотом: /confirm <i>&lt;0x…подпись&gt;</i>",
        "en": "🔗 <b>Link wallet</b>\n\nSign this in your wallet:\n<code>{text}</code>\n\nThen: /confirm <i>&lt;0x…signature&gt;</i>",
        "zh": "🔗 <b>绑定钱包</b>\n\n在钱包中签署：\n<code>{text}</code>\n\n然后：/confirm <i>&lt;0x…签名&gt;</i>",
    },
    "confirm_need_signature": {
        "ru": "Формат: /confirm <i>&lt;0x…подпись&gt;</i>",
        "en": "Format: /confirm <i>&lt;0x…signature&gt;</i>",
        "zh": "格式：/confirm <i>&lt;0x…签名&gt;</i>",
    },
    "confirm_no_nonce": {
        "ru": "❌ Сначала начни привязку: /link <i>&lt;адрес&gt;</i>",
        "en": "❌ Start linking first: /link <i>&lt;address&gt;</i>",
        "zh": "❌ 请先开始绑定：/link <i>&lt;地址&gt;</i>",
    },
    "confirm_expired": {
        "ru": "⏳ Код привязки устарел ({min} мин). Начни: /link <i>&lt;адрес&gt;</i>",
        "en": "⏳ Link code expired ({min} min). Start again: /link <i>&lt;address&gt;</i>",
        "zh": "⏳ 绑定码已过期（{min} 分钟）。请重新开始：/link <i>&lt;地址&gt;</i>",
    },
    "confirm_bad_sig": {
        "ru": "❌ Подпись не совпадает.",
        "en": "❌ Signature doesn't match.",
        "zh": "❌ 签名不匹配。",
    },
    "confirm_ok": {
        "ru": "✅ Кошелёк <code>{addr}</code> привязан.\nДепозиты зачисляются автоматически.{extra}",
        "en": "✅ Wallet <code>{addr}</code> linked.\nDeposits credit automatically.{extra}",
        "zh": "✅ 钱包 <code>{addr}</code> 已绑定。\n充值自动到账。{extra}",
    },
    "import_format": {
        "ru": "Формат: /import <i>&lt;12 или 24 слова&gt;</i>",
        "en": "Format: /import <i>&lt;12 or 24 words&gt;</i>",
        "zh": "格式：/import <i>&lt;12 或 24 个词&gt;</i>",
    },
    "import_bad_seed": {
        "ru": "❌ Сид-фраза должна содержать 12 или 24 слова.",
        "en": "❌ Seed phrase must be 12 or 24 words.",
        "zh": "❌ 助记词必须为 12 或 24 个词。",
    },
    "import_ok": {
        "ru": "✅ Кошелёк <code>{addr}</code> импортирован.\nКлюч и сид зашифрованы: /wallet export",
        "en": "✅ Wallet <code>{addr}</code> imported.\nKey and seed are encrypted: /wallet export",
        "zh": "✅ 钱包 <code>{addr}</code> 已导入。\n密钥和助记词已加密：/wallet export",
    },
    "withdraw_format": {
        "ru": "Формат: /withdraw <i>&lt;адрес&gt; &lt;сумма&gt;</i>",
        "en": "Format: /withdraw <i>&lt;address&gt; &lt;amount&gt;</i>",
        "zh": "格式：/withdraw <i>&lt;地址&gt; &lt;金额&gt;</i>",
    },
    "withdraw_min": {
        "ru": "Минимум для вывода: <b>{n} USDC</b>.",
        "en": "Minimum withdrawal: <b>{n} USDC</b>.",
        "zh": "最低提现额：<b>{n} USDC</b>。",
    },
    "withdraw_daily_limit": {
        "ru": "⏳ Лимит <b>{n} выводов в сутки</b>. Попробуй завтра.",
        "en": "⏳ Limit of <b>{n} withdrawals per day</b>. Try again tomorrow.",
        "zh": "⏳ 每日限 <b>{n} 次提现</b>。请明天再试。",
    },
    "withdraw_balance_short": {
        "ru": "❌ Недостаточно баланса. Нужно <b>{need} USDC</b> (сумма + комиссия {fee}).\nБаланс: <b>{bal} USDC</b>",
        "en": "❌ Insufficient balance. Need <b>{need} USDC</b> (amount + fee {fee}).\nBalance: <b>{bal} USDC</b>",
        "zh": "❌ 余额不足。需要 <b>{need} USDC</b>（金额 + 手续费 {fee}）。\n余额：<b>{bal} USDC</b>",
    },
    "withdraw_ok": {
        "ru": "✅ Отправлено <b>{amount} USDC</b> (комиссия {fee})\nTx: <a href=\"{tx_url}\"><code>{tx}</code></a>",
        "en": "✅ Sent <b>{amount} USDC</b> (fee {fee})\nTx: <a href=\"{tx_url}\"><code>{tx}</code></a>",
        "zh": "✅ 已发送 <b>{amount} USDC</b>（手续费 {fee}）\nTx：<a href=\"{tx_url}\"><code>{tx}</code></a>",
    },
    "withdraw_error": {
        "ru": "❌ Ошибка отправки: {error}",
        "en": "❌ Send error: {error}",
        "zh": "❌ 发送失败：{error}",
    },
    "withdraw_bad_address": {
        "ru": "❌ Непохоже на адрес Base (0x + 40 hex).",
        "en": "❌ Doesn't look like a Base address (0x + 40 hex).",
        "zh": "❌ 看起来不像 Base 地址（0x + 40 位十六进制）。",
    },
    "tx_not_found": {
        "ru": "Транзакция не найдена (ещё не mined или неверный хэш).",
        "en": "Transaction not found (not yet mined or invalid hash).",
        "zh": "交易未找到（尚未确认或哈希无效）。",
    },
    "tx_info": {
        "ru": "⛓️ <b>Транзакция на Base</b>\nОт: <code>{from_addr}</code>\nКому: <code>{to_addr}</code>\nСтатус: {status}\n{usdc_line}\n🔍 <a href=\"{url}\">Basescan</a>",
        "en": "⛓️ <b>Base transaction</b>\nFrom: <code>{from_addr}</code>\nTo: <code>{to_addr}</code>\nStatus: {status}\n{usdc_line}\n🔍 <a href=\"{url}\">Basescan</a>",
        "zh": "⛓️ <b>Base 交易</b>\n发送方：<code>{from_addr}</code>\n接收方：<code>{to_addr}</code>\n状态：{status}\n{usdc_line}\n🔍 <a href=\"{url}\">Basescan</a>",
    },
    # ----- settings extra -----
    "settings_notif_on": {
        "ru": "🔔 Уведомления включены.",
        "en": "🔔 Notifications enabled.",
        "zh": "🔔 通知已开启。",
    },
    "settings_notif_off": {
        "ru": "🔕 Уведомления отключены.",
        "en": "🔕 Notifications disabled.",
        "zh": "🔕 通知已关闭。",
    },
    "settings_react_on": {
        "ru": "⚡ Реакции-чаевые включены! Ставь 🔥/❤️/⚡ на сообщения в группах.",
        "en": "⚡ Reaction tips enabled! React 🔥/❤️/⚡ to messages in groups.",
        "zh": "⚡ 表情打赏已开启！在群聊中给消息添加 🔥/❤️/⚡ 表情。",
    },
    "settings_react_off": {
        "ru": "⚡ Реакции-чаевые отключены.",
        "en": "⚡ Reaction tips disabled.",
        "zh": "⚡ 表情打赏已关闭。",
    },
    # ----- bets -----
    "bet_format": {
        "ru": "Формат:\n• /bet create <i>&lt;вопрос&gt; | &lt;в1&gt; | &lt;в2&gt;</i>\n• /bet &lt;id&gt; &lt;номер&gt; &lt;сумма&gt;",
        "en": "Format:\n• /bet create <i>&lt;question&gt; | &lt;o1&gt; | &lt;o2&gt;</i>\n• /bet &lt;id&gt; &lt;option&gt; &lt;amount&gt;",
        "zh": "格式：\n• /bet create <i>&lt;问题&gt; | &lt;选项1&gt; | &lt;选项2&gt;</i>\n• /bet &lt;id&gt; &lt;选项&gt; &lt;金额&gt;",
    },
    "bet_create_help": {
        "ru": "Формат: /bet create <i>&lt;вопрос&gt; | &lt;в1&gt; | &lt;в2&gt;</i>\nДо 4 вариантов, дедлайн: 24h / 7d",
        "en": "Format: /bet create <i>&lt;question&gt; | &lt;o1&gt; | &lt;o2&gt;</i>\nUp to 4 options, deadline: 24h / 7d",
        "zh": "格式：/bet create <i>&lt;问题&gt; | &lt;选项1&gt; | &lt;选项2&gt;</i>\n最多 4 个选项，截止时间：24h / 7d",
    },
    "bet_max_options": {
        "ru": "Максимум 4 варианта.",
        "en": "Maximum 4 options.",
        "zh": "最多 4 个选项。",
    },
    "bet_question_long": {
        "ru": "Слишком длинный вопрос (макс 200 символов).",
        "en": "Question too long (max 200 chars).",
        "zh": "问题太长（最多 200 字符）。",
    },
    "bet_option_long": {
        "ru": "Вариант длиннее {n} символов: <i>{o}…</i>",
        "en": "Option longer than {n} chars: <i>{o}…</i>",
        "zh": "选项超过 {n} 个字符：<i>{o}…</i>",
    },
    "bet_created": {
        "ru": "🎲 Ставка #{id} создана!",
        "en": "🎲 Bet #{id} created!",
        "zh": "🎲 投注 #{id} 已创建！",
    },
    "bet_deadline_to": {
        "ru": "\n⏰ Приём ставок до: {time}",
        "en": "\n⏰ Bets accepted until: {time}",
        "zh": "\n⏰ 投注截止时间：{time}",
    },
    "bet_no_deadline": {
        "ru": "\n⌛ Закрытие: /resolve (только ты)",
        "en": "\n⌛ Closing: /resolve (you only)",
        "zh": "\n⌛ 结算方式：/resolve（仅创建者）",
    },
    "bet_howto": {
        "ru": "Ставят: /bet {id} &lt;номер&gt; &lt;сумма&gt; или кнопки: /bets",
        "en": "Bet: /bet {id} &lt;option&gt; &lt;amount&gt; or use buttons: /bets",
        "zh": "下注：/bet {id} &lt;选项&gt; &lt;金额&gt; 或使用按钮：/bets",
    },
    "bet_confirmed": {
        "ru": "✅ Ставка принята!\n🎯 #{id} — <b>{label}</b> на <b>{amount} USDC</b>\nОстаток: {bal} USDC",
        "en": "✅ Bet accepted!\n🎯 #{id} — <b>{label}</b> for <b>{amount} USDC</b>\nBalance: {bal} USDC",
        "zh": "✅ 投注已接受！\n🎯 #{id} —— <b>{label}</b> 下注 <b>{amount} USDC</b>\n余额：{bal} USDC",
    },
    "bet_not_found": {
        "ru": "Ставка не найдена.",
        "en": "Bet not found.",
        "zh": "投注未找到。",
    },
    "bet_bad_option": {
        "ru": "Неверный номер варианта.",
        "en": "Invalid option number.",
        "zh": "无效选项编号。",
    },
    "bet_closed": {
        "ru": "Эта ставка уже закрыта.",
        "en": "This bet is already closed.",
        "zh": "此投注已关闭。",
    },
    "bet_deadline_passed": {
        "ru": "⏰ Время приёма ставок истекло. Жди решения: /bets",
        "en": "⏰ Betting period ended. Wait for resolution: /bets",
        "zh": "⏰ 投注时间已结束。等待结算：/bets",
    },
    "bet_resolved": {
        "ru": "✅ <b>Решён:</b> {winner}",
        "en": "✅ <b>Resolved:</b> {winner}",
        "zh": "✅ <b>已结算：</b> {winner}",
    },
    "bet_cancelled": {
        "ru": "❌ Отменён — деньги возвращены всем.",
        "en": "❌ Cancelled — funds returned to all.",
        "zh": "❌ 已取消——资金已退还。",
    },
    "bet_expired": {
        "ru": "🕳️ <b>Рынок истёк</b> — вернуть: /cancel {id}",
        "en": "🕳️ <b>Market expired</b> — refund: /cancel {id}",
        "zh": "🕳️ <b>市场已过期</b> —— 退款：/cancel {id}",
    },
    "bet_pot": {
        "ru": "Пул итого: <b>{pot} USDC</b> · участников: {backers}",
        "en": "Total pool: <b>{pot} USDC</b> · backers: {backers}",
        "zh": "奖池总额：<b>{pot} USDC</b> · 参与者：{backers}",
    },
    "bet_fee_note": {
        "ru": "Комиссия на выигрыш: 2% (создателю рынка)",
        "en": "Win fee: 2% (to market creator)",
        "zh": "赢利手续费：2%（归创建者）",
    },
    "bet_empty": {
        "ru": "🎲 Открытых ставок нет.\nСоздай: /bet create <вопрос> | <в1> | <в2>",
        "en": "🎲 No open bets.\nCreate: /bet create <question> | <o1> | <o2>",
        "zh": "🎲 暂无开放投注。\n创建：/bet create <问题> | <选项1> | <选项2>",
    },
    "bet_list_header": {
        "ru": "🎲 <b>Открытые ставки</b>",
        "en": "🎲 <b>Open bets</b>",
        "zh": "🎲 <b>开放投注</b>",
    },
    "bet_list_expired": {
        "ru": " 🕳️ истёк — /cancel {id}",
        "en": " 🕳️ expired — /cancel {id}",
        "zh": " 🕳️ 已过期 —— /cancel {id}",
    },
    "bet_list_hint": {
        "ru": "Нажми на рынок — карточка со ставками.",
        "en": "Tap a market for bet details.",
        "zh": "点击市场查看详情。",
    },
    "bet_resolve_title": {
        "ru": "🏁 <b>Закрыть #{id}</b> — «{question}»\n\nКто победил?\nПобедители делят пул ({pot} USDC), создатель получает 2%.",
        "en": "🏁 <b>Close #{id}</b> — \"{question}\"\n\nWho won?\nWinners share pool ({pot} USDC), creator gets 2%.",
        "zh": "🏁 <b>关闭 #{id}</b> ——「{question}」\n\n谁赢了？\n赢家瓜分奖池（{pot} USDC），创建者获得 2%。",
    },
    "bet_resolved_header": {
        "ru": "✅ Ставка #{id} закрыта!",
        "en": "✅ Bet #{id} resolved!",
        "zh": "✅ 投注 #{id} 已结算！",
    },
    "bet_payouts_sent": {
        "ru": "Выплаты разосланы победителям.",
        "en": "Payouts sent to winners.",
        "zh": "奖金已发送给赢家。",
    },
    "bet_amount_ask": {
        "ru": "🎯 #{id}: {question}\n\n<b>{label}</b> — сколько ставим?",
        "en": "🎯 #{id}: {question}\n\n<b>{label}</b> — how much?",
        "zh": "🎯 #{id}：{question}\n\n<b>{label}</b> —— 下注多少？",
    },
    "bet_my_empty": {
        "ru": "🎲 У тебя нет открытых позиций. Ставят: /bets",
        "en": "🎲 No open positions. Bets: /bets",
        "zh": "🎲 没有持仓。投注：/bets",
    },
    "bet_my_header": {
        "ru": "📌 <b>Твои открытые позиции</b>",
        "en": "📌 <b>Your open positions</b>",
        "zh": "📌 <b>你的持仓</b>",
    },
    "bet_notify_win": {
        "ru": "🏆 <b>Ты выиграл {amount} USDC!</b>\n🎲 #{id} — «{question}»\nПобедил: <b>{winner}</b>\nБаланс: /balance",
        "en": "🏆 <b>You won {amount} USDC!</b>\n🎲 #{id} — \"{question}\"\nWinner: <b>{winner}</b>\nBalance: /balance",
        "zh": "🏆 <b>你赢了 {amount} USDC！</b>\n🎲 #{id} ——「{question}」\n获胜者：<b>{winner}</b>\n余额：/balance",
    },
    "bet_notify_lose": {
        "ru": "🎲 #{id} — «{question}» закрыта.\nПобедил: <b>{winner}</b>\nТвои ставки на «{labels}» не сыграли. /bets",
        "en": "🎲 #{id} — \"{question}\" resolved.\nWinner: <b>{winner}</b>\nYour bets on \"{labels}\" didn't win. /bets",
        "zh": "🎲 #{id} ——「{question}」已结算。\n获胜者：<b>{winner}</b>\n你对「{labels}」的投注未中奖。/bets",
    },
    "bet_notify_cancel": {
        "ru": "↩️ Ставка #{id} — «{question}» отменена.\nДеньги возвращены: /history",
        "en": "↩️ Bet #{id} — \"{question}\" cancelled.\nFunds returned: /history",
        "zh": "↩️ 投注 #{id} ——「{question}」已取消。\n资金已退还：/history",
    },
    "cancel_format": {
        "ru": "Формат: /cancel &lt;id&gt;",
        "en": "Format: /cancel &lt;id&gt;",
        "zh": "格式：/cancel &lt;id&gt;",
    },
    "potential_win": {
        "ru": "потенциальный выигрыш: <b>{amt} USDC</b>",
        "en": "potential win: <b>{amt} USDC</b>",
        "zh": "潜在赢额：<b>{amt} USDC</b>",
    },
    "your_stake": {
        "ru": "твоя ставка: {amt}",
        "en": "your stake: {amt}",
        "zh": "你的下注：{amt}",
    },
    "resolve_format": {
        "ru": "Формат: /resolve &lt;id&gt; &lt;номер&gt;",
        "en": "Format: /resolve &lt;id&gt; &lt;option&gt;",
        "zh": "格式：/resolve &lt;id&gt; &lt;选项&gt;",
    },
    # ----- common errors -----
    "amount_positive": {
        "ru": "Сумма должна быть больше нуля.",
        "en": "Amount must be greater than zero.",
        "zh": "金额必须大于零。",
    },
    "no_balance": {
        "ru": "❌ Недостаточно баланса. Пополни: /deposit",
        "en": "❌ Insufficient balance. Top up: /deposit",
        "zh": "❌ 余额不足。充值：/deposit",
    },
    "bad_amount": {
        "ru": "Неверная сумма",
        "en": "Invalid amount",
        "zh": "无效金额",
    },
    "error_generic": {
        "ru": "Что-то пошло не так",
        "en": "Something went wrong",
        "zh": "出错了",
    },
    # ----- button labels (extra) -----
    "btn_mk_create": {
        "ru": "➕ Создать рынок",
        "en": "➕ Create market",
        "zh": "➕ 创建市场",
    },
    "btn_close_market": {
        "ru": "Закрыть рынок",
        "en": "Close market",
        "zh": "关闭市场",
    },
    "btn_all_markets": {
        "ru": "🎲 Все рынки",
        "en": "🎲 All markets",
        "zh": "🎲 全部市场",
    },
    "btn_market": {
        "ru": "🎯 Рынок",
        "en": "🎯 Market",
        "zh": "🎯 市场",
    },
    "market_not_found": {
        "ru": "Рынок не найден",
        "en": "Market not found",
        "zh": "市场未找到",
    },
    "market_closed": {
        "ru": "Рынок уже закрыт",
        "en": "Market already closed",
        "zh": "市场已关闭",
    },
    "market_only_creator": {
        "ru": "Закрыть может только создатель",
        "en": "Only the creator can close",
        "zh": "只有创建者才能关闭",
    },
    # ----- ai handler -----
    "ai_disabled": {
        "ru": "🧠 ИИ-ассистент пока не подключён.\n\nБесплатно: создай ключ на <a href=\"https://console.groq.com\">console.groq.com</a> (100K запросов/день) и добавь в .env:\n<code>AI_API_KEY=gsk_...</code>",
        "en": "🧠 AI assistant is not configured yet.\n\nFree: get a key at <a href=\"https://console.groq.com\">console.groq.com</a> (100K req/day) and add to .env:\n<code>AI_API_KEY=gsk_...</code>",
        "zh": "🧠 AI 助手尚未配置。\n\n免费：在 <a href=\"https://console.groq.com\">console.groq.com</a> 获取密钥（每天 10 万次请求），添加到 .env：\n<code>AI_API_KEY=gsk_...</code>",
    },
    "ai_question_empty": {
        "ru": "🧠 Спроси меня о чём угодно.\nФормат: <code>/ask вопрос</code>\nМожно ответом на сообщение — возьму контекст.",
        "en": "🧠 Ask me anything.\nFormat: <code>/ask question</code>\nReply to a message for context.",
        "zh": "🧠 随便问。\n格式：<code>/ask 问题</code>\n回复消息可提供上下文。",
    },
    "ai_question_long": {
        "ru": "Слишком длинный вопрос (макс {n} символов).",
        "en": "Question too long (max {n} chars).",
        "zh": "问题太长（最多 {n} 个字符）。",
    },
    "ai_error": {
        "ru": "🤖 ИИ недоступен: <i>{error}</i>\nПопробуй позже.",
        "en": "🤖 AI unavailable: <i>{error}</i>\nTry again later.",
        "zh": "🤖 AI 不可用：<i>{error}</i>\n请稍后重试。",
    },
    "ai_empty_answer": {
        "ru": "🤖 Пустой ответ от ИИ. Попробуй переформулировать.",
        "en": "🤖 Empty response from AI. Try rephrasing.",
        "zh": "🤖 AI 返回空答案。请换个说法。",
    },
    # ----- menu extra -----
    "betcreate_hint": {
        "ru": "🎲 <b>Создание рынка</b>\n\n/bet create <вопрос> | <в1> | <в2> [24h]\n\nДо 4 вариантов, опционально дедлайн. Создатель получает 2% от выигрыша.",
        "en": "🎲 <b>Create a bet</b>\n\n/bet create <question> | <o1> | <o2> [24h]\n\nUp to 4 options, optional deadline. Creator gets 2% of winnings.",
        "zh": "🎲 <b>创建投注</b>\n\n/bet create <问题> | <选项1> | <选项2> [24h]\n\n最多 4 个选项，可设截止时间。创建者获得赢家 2% 手续费。",
    },
    "paywall_empty": {
        "ru": "🔐 Платных постов пока нет. Создай первый: /paywall create 5 Заголовок",
        "en": "🔐 No paid posts yet. Create the first: /paywall create 5 Title",
        "zh": "🔐 暂无付费内容。创建第一个：/paywall create 5 标题",
    },
    "paywall_list_header": {
        "ru": "🔐 <b>Платные посты</b>",
        "en": "🔐 <b>Paid posts</b>",
        "zh": "🔐 <b>付费内容</b>",
    },
    "paywall_buy_ok": {
        "ru": "✅ Куплено за {amount} USDC.\n\n{content}",
        "en": "✅ Purchased for {amount} USDC.\n\n{content}",
        "zh": "✅ 已购买，花费 {amount} USDC。\n\n{content}",
    },
    "paywall_buy_dup": {
        "ru": "🔓 Уже куплено. Контент:\n\n{content}",
        "en": "🔓 Already purchased. Content:\n\n{content}",
        "zh": "🔓 已购买。内容：\n\n{content}",
    },
    "paywall_buy_self": {
        "ru": "❌ Это твой пост — покупать его не нужно.",
        "en": "❌ This is your own post — no need to buy it.",
        "zh": "❌ 这是你自己的帖子——无需购买。",
    },
    "paywall_buy_insufficient": {
        "ru": "❌ Недостаточно средств: нужно {amount} USDC. Пополни: /deposit",
        "en": "❌ Insufficient funds: need {amount} USDC. Top up: /deposit",
        "zh": "❌ 余额不足：需要 {amount} USDC。充值：/deposit",
    },
    "donate_landing": {
        "ru": "💛 Поддержать <b>@{user}</b>\n\nОтправь USDC (<b>сеть Base</b>) на адрес:\n<code>{addr}</code>\n\nЗачислится автоматически после /link. Чаевые: /tip 5 @{user}",
        "en": "💛 Support <b>@{user}</b>\n\nSend USDC (<b>Base network</b>) to:\n<code>{addr}</code>\n\nCredits automatically after /link. Tip: /tip 5 @{user}",
        "zh": "💛 支持 <b>@{user}</b>\n\n将 USDC（<b>Base 网络</b>）发送到：\n<code>{addr}</code>\n\n/link 后自动到账。打赏：/tip 5 @{user}",
    },
    "deep_link_market": {
        "ru": "🎯 Ты пришёл по ссылке на рынок!",
        "en": "🎯 You followed a market link!",
        "zh": "🎯 你通过市场链接进入了！",
    },
    "deep_link_market_not_found": {
        "ru": "🎲 Рынок не найден или ещё не открыт.\nОткрытые: /bets",
        "en": "🎲 Market not found or not yet open.\nOpen: /bets",
        "zh": "🎲 市场未找到或尚未开放。\n开放市场：/bets",
    },
    "deep_link_paywall": {
        "ru": "🔐 Ты пришёл по ссылке на платный пост!",
        "en": "🔐 You followed a paid post link!",
        "zh": "🔐 你通过付费内容链接进入了！",
    },
    "deep_link_paywall_not_found": {
        "ru": "🔐 Пост не найден. Все посты: /paywall list",
        "en": "🔐 Post not found. All posts: /paywall list",
        "zh": "🔐 内容未找到。全部内容：/paywall list",
    },
    "broadcast_format": {
        "ru": "Формат: /broadcast <текст>",
        "en": "Format: /broadcast <text>",
        "zh": "格式：/broadcast <文本>",
    },
    "broadcast_sent": {
        "ru": "📣 Разослано: {n} пользователям.",
        "en": "📣 Sent to {n} users.",
        "zh": "📣 已发送给 {n} 位用户。",
    },
    "broadcast_admin_only": {
        "ru": "❌ Только для владельца бота.",
        "en": "❌ Bot owner only.",
        "zh": "❌ 仅限机器人所有者。",
    },
    # ----- deposit notification -----
    "deposit_notified": {
        "ru": "✅ Депозит зачислен: <b>{amount} USDC</b>\nTx: <a href=\"{tx_url}\"><code>{tx}</code></a>\nБаланс: /balance",
        "en": "✅ Deposit credited: <b>{amount} USDC</b>\nTx: <a href=\"{tx_url}\"><code>{tx}</code></a>\nBalance: /balance",
        "zh": "✅ 充值已到账：<b>{amount} USDC</b>\nTx：<a href=\"{tx_url}\"><code>{tx}</code></a>\n余额：/balance",
    },
    # ----- market deadline notification -----
    "deadline_notify": {
        "ru": "⏰ Рынок #{id} — «{question}» достиг дедлайна.\nЗакрой: /resolve {id} <номер>.\nИначе после grace-периода любой вернёт деньги.",
        "en": "⏰ Market #{id} — \"{question}\" hit the deadline.\nResolve: /resolve {id} <option>.\nOtherwise anyone can refund after grace period.",
        "zh": "⏰ 市场 #{id} ——「{question}」已到截止时间。\n结算：/resolve {id} <选项>。\n否则宽限期后任何人都可以退款。",
    },
    "grace_warn": {
        "ru": "⚠️ Рынок #{id} — «{question}»: через {hours}ч истекает grace-период.\nЛюбой сможет вернуть деньги: /cancel {id}",
        "en": "⚠️ Market #{id} — \"{question}\": grace period ends in {hours}h.\nAnyone can then refund: /cancel {id}",
        "zh": "⚠️ 市场 #{id} ——「{question}」：宽限期将在 {hours} 小时后结束。\n届时任何人都可以退款：/cancel {id}",
    },
    # ----- throttle -----
    "throttle": {
        "ru": "⏳ Слишком часто. Подожди {sec} сек.",
        "en": "⏳ Too fast. Wait {sec}s.",
        "zh": "⏳ 操作太频繁。请等待 {sec} 秒。",
    },
    # ----- market creation validation -----
    "market_min_options": {
        "ru": "Нужно минимум 2 варианта: | вариант1 | вариант2",
        "en": "Need at least 2 options: | option1 | option2",
        "zh": "至少需要 2 个选项：| 选项1 | 选项2",
    },
    "market_max_options": {
        "ru": "Максимум 4 варианта.",
        "en": "Maximum 4 options.",
        "zh": "最多 4 个选项。",
    },
    "market_question_long": {
        "ru": "Слишком длинный вопрос (макс 200 символов).",
        "en": "Question too long (max 200 chars).",
        "zh": "问题太长（最多 200 字符）。",
    },
    "market_option_long": {
        "ru": "Вариант длиннее {n} символов: <i>{o}\u2026</i>",
        "en": "Option longer than {n} chars: <i>{o}\u2026</i>",
        "zh": "选项超过 {n} 个字符：<i>{o}\u2026</i>",
    },
    "market_trade_help": {
        "ru": "Формат:\n\u2022 /trade &lt;id&gt; &lt;номер&gt; &lt;сумма&gt;\n\u2022 /sell &lt;id&gt; &lt;номер&gt; [процент]\n\u2022 /markets",
        "en": "Format:\n\u2022 /trade &lt;id&gt; &lt;option&gt; &lt;amount&gt;\n\u2022 /sell &lt;id&gt; &lt;option&gt; [percent]\n\u2022 /markets",
        "zh": "格式：\n\u2022 /trade &lt;id&gt; &lt;选项&gt; &lt;金额&gt;\n\u2022 /sell &lt;id&gt; &lt;选项&gt; [百分比]\n\u2022 /markets",
    },
    "sell_format": {
        "ru": "Формат: /sell &lt;id&gt; &lt;номер&gt; [процент%]",
        "en": "Format: /sell &lt;id&gt; &lt;option&gt; [percent%]",
        "zh": "格式：/sell &lt;id&gt; &lt;选项&gt; [百分比%]",
    },
    "positions_empty": {
        "ru": "\U0001f4c8 У тебя нет позиций. Рынки: /markets",
        "en": "\U0001f4c8 No positions. Markets: /markets",
        "zh": "\U0001f4c8 没有持仓。市场：/markets",
    },
    "paywall_format_create": {
        "ru": "Формат: /paywall create &lt;цена&gt; &lt;заголовок&gt;",
        "en": "Format: /paywall create &lt;price&gt; &lt;title&gt;",
        "zh": "格式：/paywall create &lt;价格&gt; &lt;标题&gt;",
    },
    "paywall_not_found": {
        "ru": "Пост не найден.",
        "en": "Post not found.",
        "zh": "内容未找到。",
    },
    "paywall_format_buy": {
        "ru": "Формат: /paywall buy &lt;id&gt;",
        "en": "Format: /paywall buy &lt;id&gt;",
        "zh": "格式：/paywall buy &lt;id&gt;",
    },
    "paywall_need_content": {
        "ru": "Пришли контент текстом или подписью к фото.",
        "en": "Send the content as text or a photo caption.",
        "zh": "请以文字或图片说明形式发送内容。",
    },
    "paywall_no_channels": {
        "ru": "Платных каналов пока нет.",
        "en": "No paid channels yet.",
        "zh": "暂无付费频道。",
    },
    "paywall_sub_format": {
        "ru": "Формат: /paywall subscribe @канал",
        "en": "Format: /paywall subscribe @channel",
        "zh": "格式：/paywall subscribe @频道",
    },
    "paywall_channel_not_found": {
        "ru": "Канал не найден.",
        "en": "Channel not found.",
        "zh": "频道未找到。",
    },
    "claim_format": {
        "ru": "Формат: /claim &lt;0x...tx_hash&gt;",
        "en": "Format: /claim &lt;0x...tx_hash&gt;",
        "zh": "格式：/claim &lt;0x...tx_hash&gt;",
    },
    "admin_only": {
        "ru": "Только владелец бота.",
        "en": "Bot owner only.",
        "zh": "仅限机器人所有者。",
    },
    "market_min_bank": {
        "ru": "Минимальный банк: <b>{n} USDC</b> (ликвидность AMM — вернётся с прибылью).",
        "en": "Minimum bank: <b>{n} USDC</b> (AMM liquidity — returned with profit).",
        "zh": "最低资金：<b>{n} USDC</b>（AMM 流动性——会连本带利返还）。",
    },
    "market_max_bank": {
        "ru": "Максимальный банк: <b>{n} USDC</b>.",
        "en": "Maximum bank: <b>{n} USDC</b>.",
        "zh": "最高资金：<b>{n} USDC</b>。",
    },
    "market_deadline_fmt": {
        "ru": "\n⏰ Дедлайн: {time}",
        "en": "\n⏰ Deadline: {time}",
        "zh": "\n⏰ 截止时间：{time}",
    },
    "market_no_deadline_card": {
        "ru": "\n⌛ Закрытие: /resolve-кнопка на карточке (только ты)",
        "en": "\n⌛ Closing: /resolve button on card (you only)",
        "zh": "\n⌛ 结算方式：卡片上的 /resolve 按钮（仅创建者）",
    },
    "market_created_msg": {
        "ru": "📈 Рынок #{id} создан!\n\n<b>{question}</b>\n{options}\n{liquidity}\n{deadline}\n\n{hint}",
        "en": "📈 Market #{id} created!\n\n<b>{question}</b>\n{options}\n{liquidity}\n{deadline}\n\n{hint}",
        "zh": "📈 市场 #{id} 已创建！\n\n<b>{question}</b>\n{options}\n{liquidity}\n{deadline}\n\n{hint}",
    },
    "market_liquidity": {
        "ru": "🏦 Ликвидность: {amount} USDC (твоё)",
        "en": "🏦 Liquidity: {amount} USDC (yours)",
        "zh": "🏦 流动性：{amount} USDC（你的）",
    },
    "market_trade_hint": {
        "ru": "Торговля: /trade {id} <номер> <сумма> или кнопки: /markets\nЦены движутся с спросом — как на Polymarket.",
        "en": "Trade: /trade {id} <option> <amount> or use buttons: /markets\nPrices move with demand — like Polymarket.",
        "zh": "交易：/trade {id} <选项> <金额> 或使用按钮：/markets\n价格随供需波动——类似 Polymarket。",
    },
    "market_card_resolved": {
        "ru": "✅ <b>Решён:</b> {label}",
        "en": "✅ <b>Resolved:</b> {label}",
        "zh": "✅ <b>已结算：</b> {label}",
    },
    "market_card_cancelled": {
        "ru": "❌ Отменён — деньги возвращены.",
        "en": "❌ Cancelled — funds returned.",
        "zh": "❌ 已取消——资金已退还。",
    },
    "market_card_deadline_passed": {
        "ru": "⏰ Дедлайн прошёл — ждём решения создателя.",
        "en": "⏰ Deadline passed — awaiting creator's resolution.",
        "zh": "⏰ 截止时间已过——等待创建者结算。",
    },
    "market_your_shares": {
        "ru": "\n     └ твои доли: {shares} (≈{value} USDC)",
        "en": "\n     └ your shares: {shares} (≈{value} USDC)",
        "zh": "\n     └ 你的份额：{shares}（≈{value} USDC）",
    },
    "market_liquidity_pool": {
        "ru": "🏦 Пул ликвидности: {amount} USDC",
        "en": "🏦 Liquidity pool: {amount} USDC",
        "zh": "🏦 流动性资金池：{amount} USDC",
    },
    "market_resolution_note": {
        "ru": "💰 Победные доли платят 1 USDC за долю при резолюции. Продать можно в любой момент.",
        "en": "💰 Winning shares pay 1 USDC each at resolution. Sell anytime.",
        "zh": "💰 获胜份额在结算时每份支付 1 USDC。可随时卖出。",
    },
    "market_fav": {
        "ru": " — фаворит: <b>{leader}</b>",
        "en": " — leader: <b>{leader}</b>",
        "zh": " — 领先：<b>{leader}</b>",
    },
    "market_no_shares": {
        "ru": "У тебя нет долей этого исхода. Позиции: /positions",
        "en": "You don't hold shares of this outcome. Positions: /positions",
        "zh": "你没有持有此选项的份额。持仓：/positions",
    },
    "market_no_shares_short": {
        "ru": "Нет долей",
        "en": "No shares",
        "zh": "无份额",
    },
    "market_too_little": {
        "ru": "Слишком мало",
        "en": "Too little",
        "zh": "太少",
    },
    "market_sell_error": {
        "ru": "Не получилось продать",
        "en": "Could not sell",
        "zh": "卖出失败",
    },
    "market_closed_header": {
        "ru": "✅ <b>Рынок #{id} закрыт!</b>",
        "en": "✅ <b>Market #{id} closed!</b>",
        "zh": "✅ <b>市场 #{id} 已关闭！</b>",
    },
    "market_sell_hint": {
        "ru": "Продать: /sell {mid} {opt}",
        "en": "Sell: /sell {mid} {opt}",
        "zh": "卖出：/sell {mid} {opt}",
    },
    "market_sell_pct_format": {
        "ru": "Процент: 1–100 (например /sell 3 1 50%)",
        "en": "Percent: 1–100 (e.g. /sell 3 1 50%)",
        "zh": "百分比：1–100（例如 /sell 3 1 50%）",
    },
    "market_positions_header": {
        "ru": "📌 <b>Твои позиции на рынках</b>\n",
        "en": "📌 <b>Your market positions</b>\n",
        "zh": "📌 <b>你的市场持仓</b>\n",
    },
    "market_position_line": {
        "ru": "   • {option} — {shares} долей @ {price}\n   • стоимость ≈ <b>{value} USDC</b> (PnL {pnl})",
        "en": "   • {option} — {shares} shares @ {price}\n   • value ≈ <b>{value} USDC</b> (PnL {pnl})",
        "zh": "   • {option} — {shares} 份 @ {price}\n   • 估值 ≈ <b>{value} USDC</b>（盈亏 {pnl}）",
    },
    "market_total_value": {
        "ru": "\nΣ стоимость: ≈<b>{value} USDC</b>",
        "en": "\nΣ value: ≈<b>{value} USDC</b>",
        "zh": "\nΣ 估值：≈<b>{value} USDC</b>",
    },
    "btn_buy": {
        "ru": "🛒 Купить",
        "en": "🛒 Buy",
        "zh": "🛒 购买",
    },
    "btn_sell": {
        "ru": "📉 Продать",
        "en": "📉 Sell",
        "zh": "📉 卖出",
    },
    "btn_cancel_action": {
        "ru": "✖️ Отменить",
        "en": "✖️ Cancel",
        "zh": "✖️ 取消",
    },
    "btn_buy_amount": {
        "ru": "Купить {amount} USDC",
        "en": "Buy {amount} USDC",
        "zh": "购买 {amount} USDC",
    },
    "btn_sell_pct": {
        "ru": "Продать {pct}%",
        "en": "Sell {pct}%",
        "zh": "卖出 {pct}%",
    },
    "btn_winner": {
        "ru": "🏆 {label}",
        "en": "🏆 {label}",
        "zh": "🏆 {label}",
    },
    "btn_back_short": {
        "ru": "◀️ Назад",
        "en": "◀️ Back",
        "zh": "◀️ 返回",
    },
    "btn_all_markets_v2": {
        "ru": "📈 Все рынки",
        "en": "📈 All markets",
        "zh": "📈 全部市场",
    },
    "paywall_price_range": {
        "ru": "Цена должна быть 0 < цена ≤ {max} USDC",
        "en": "Price must be 0 < price ≤ {max} USDC",
        "zh": "价格必须为 0 < 价格 ≤ {max} USDC",
    },
    "paywall_title_too_long": {
        "ru": "⚠️ Заголовок слишком длинный: максимум {n} символов.",
        "en": "⚠️ Title too long: max {n} characters.",
        "zh": "⚠️ 标题太长：最多 {n} 个字符。",
    },
    "paywall_draft_ok": {
        "ru": "💰 {amount} USDC · «{title}»\n\nТеперь пришли <b>контент</b> одним сообщением (текст).\n/paywall cancel — отмена.",
        "en": "💰 {amount} USDC · «{title}»\n\nNow send the <b>content</b> as one message (text).\n/paywall cancel — cancel.",
        "zh": "💰 {amount} USDC ·「{title}」\n\n现在请发送<b>内容</b>（文字）。\n/paywall cancel — 取消。",
    },
    "paywall_cancel_created": {
        "ru": "❌ Создание отменено.",
        "en": "❌ Creation cancelled.",
        "zh": "❌ 创建已取消。",
    },
    "paywall_no_active": {
        "ru": "Нет активного создания.",
        "en": "No active creation.",
        "zh": "没有进行中的创建。",
    },
    "paywall_list_buy_hint": {
        "ru": "Купить: /paywall buy <id>",
        "en": "Buy: /paywall buy <id>",
        "zh": "购买：/paywall buy <id>",
    },
    "paywall_bought_for": {
        "ru": "✅ Куплено за {amount} USDC.\n\n{content}",
        "en": "✅ Purchased for {amount} USDC.\n\n{content}",
        "zh": "✅ 已购买，花费 {amount} USDC。\n\n{content}",
    },
    "paywall_already_bought": {
        "ru": "🔓 Уже куплено. Контент:\n\n{content}",
        "en": "🔓 Already purchased. Content:\n\n{content}",
        "zh": "🔓 已购买。内容：\n\n{content}",
    },
    "paywall_own_post": {
        "ru": "❌ Это твой пост — покупать его не нужно.",
        "en": "❌ This is your own post — no need to buy it.",
        "zh": "❌ 这是你自己的帖子——无需购买。",
    },
    "paywall_insufficient": {
        "ru": "❌ Недостаточно средств: нужно {amount} USDC.\nПополни: /deposit",
        "en": "❌ Insufficient funds: need {amount} USDC.\nTop up: /deposit",
        "zh": "❌ 余额不足：需要 {amount} USDC。\n充值：/deposit",
    },
    "paywall_need_admin_channel": {
        "ru": "⚠️ Выполняй команду <b>в самом канале</b>, где бот — админ.",
        "en": "⚠️ Run the command <b>in the channel itself</b> where the bot is an admin.",
        "zh": "⚠️ 请在<b>频道内</b>执行命令，机器人需为管理员。",
    },
    "paywall_bot_admin": {
        "ru": "⚠️ Сначала сделай бота <b>админом канала</b>.",
        "en": "⚠️ Make the bot a <b>channel admin</b> first.",
        "zh": "⚠️ 请先将机器人设为<b>频道管理员</b>。",
    },
    "paywall_user_admin": {
        "ru": "⚠️ Только админ канала может включить продажу доступа.",
        "en": "⚠️ Only a channel admin can enable paid access.",
        "zh": "⚠️ 只有频道管理员才能开启付费访问。",
    },
    "paywall_check_error": {
        "ru": "⚠️ Не удалось проверить права. Попробуй ещё раз.",
        "en": "⚠️ Could not check permissions. Try again.",
        "zh": "⚠️ 权限检查失败。请重试。",
    },
    "paywall_channel_disabled": {
        "ru": "📡 Продажа доступа к каналу <b>выключена</b>. Подписки продолжают действовать.",
        "en": "📡 Paid access to the channel is <b>disabled</b>. Existing subscriptions remain active.",
        "zh": "📡 频道付费访问已<b>关闭</b>。现有订阅继续有效。",
    },
    "paywall_channel_format": {
        "ru": "Формат: /paywall channel <цена USDC за 30 дней> или /paywall channel off",
        "en": "Format: /paywall channel <price USDC per 30 days> or /paywall channel off",
        "zh": "格式：/paywall channel <每 30 天 USDC 价格> 或 /paywall channel off",
    },
    "paywall_channel_limit": {
        "ru": "⚠️ Лимит: не больше {n} платных каналов на юзера.",
        "en": "⚠️ Limit: no more than {n} paid channels per user.",
        "zh": "⚠️ 限制：每个用户最多 {n} 个付费频道。",
    },
    "paywall_channel_active": {
        "ru": "📡 Канал продаётся: <b>{amount} USDC / 30 дней</b>.\nПодписчики: /paywall subscribe @{channel}",
        "en": "📡 Channel priced at: <b>{amount} USDC / 30 days</b>.\nSubscribers: /paywall subscribe @{channel}",
        "zh": "📡 频道定价：<b>{amount} USDC / 30 天</b>。\n订阅：/paywall subscribe @{channel}",
    },
    "paywall_subscribe_format": {
        "ru": "Формат: /paywall subscribe @канал",
        "en": "Format: /paywall subscribe @channel",
        "zh": "格式：/paywall subscribe @频道",
    },
    "paywall_channel_not_found_msg": {
        "ru": "❌ Канал не найден.",
        "en": "❌ Channel not found.",
        "zh": "❌ 频道未找到。",
    },
    "paywall_not_selling": {
        "ru": "❌ Этот канал не продаётся.",
        "en": "❌ This channel is not for sale.",
        "zh": "❌ 此频道未出售。",
    },
    "paywall_you_are_admin": {
        "ru": "⚠️ Ты админ канала — подписка не нужна.",
        "en": "⚠️ You're a channel admin — no subscription needed.",
        "zh": "⚠️ 你是频道管理员——无需订阅。",
    },
    "paywall_self_channel": {
        "ru": "❌ Это твой канал — подписка не нужна.",
        "en": "❌ This is your channel — no subscription needed.",
        "zh": "❌ 这是你自己的频道——无需订阅。",
    },
    "paywall_subscribe_insufficient": {
        "ru": "❌ Недостаточно средств: нужно {amount} USDC.\nПополни: /deposit",
        "en": "❌ Insufficient funds: need {amount} USDC.\nTop up: /deposit",
        "zh": "❌ 余额不足：需要 {amount} USDC。\n充值：/deposit",
    },
    "paywall_subscribe_fail": {
        "ru": "❌ Не получилось оформить подписку.",
        "en": "❌ Could not process subscription.",
        "zh": "❌ 订阅处理失败。",
    },
    "paywall_access_renewed": {
        "ru": "🔑 Доступ продлён до <b>{until}</b>. Остаёшься в канале.",
        "en": "🔑 Access renewed until <b>{until}</b>. You stay in the channel.",
        "zh": "🔑 访问已续期至 <b>{until}</b>。你将继续留在频道中。",
    },
    "paywall_access_invite": {
        "ru": "🔑 Доступ к каналу до <b>{until}</b>.\nЖми ссылку (действует 1 час): {link}",
        "en": "🔑 Channel access until <b>{until}</b>.\nClick the link (valid 1 hour): {link}",
        "zh": "🔑 频道访问权限至 <b>{until}</b>。\n点击链接（1 小时有效）：{link}",
    },
    "paywall_access_manual": {
        "ru": "🔑 Оплачено. Доступ до <b>{until}</b>. Бот сам откроет доступ (проверь, что бот — админ канала).",
        "en": "🔑 Paid. Access until <b>{until}</b>. The bot will grant access (make sure it's a channel admin).",
        "zh": "🔑 已付款。访问权限至 <b>{until}</b>。机器人将自动授予访问权限（请确认机器人是频道管理员）。",
    },
    "paywall_owner_notified": {
        "ru": "💰 +{amount} USDC — подписка на канал «{title}».",
        "en": "💰 +{amount} USDC — subscription to channel \"{title}\".",
        "zh": "💰 +{amount} USDC — 频道「{title}」订阅。",
    },
    "paywall_channels_empty": {
        "ru": "📡 Платных каналов пока нет. Админ: /paywall channel 5 (в канале).",
        "en": "📡 No paid channels yet. Admin: /paywall channel 5 (in the channel).",
        "zh": "📡 暂无付费频道。管理员：/paywall channel 5（在频道内）。",
    },
    "paywall_channels_header": {
        "ru": "📡 <b>Платные каналы</b>\n\n{lines}\n\nКупить: /paywall subscribe @канал",
        "en": "📡 <b>Paid channels</b>\n\n{lines}\n\nSubscribe: /paywall subscribe @channel",
        "zh": "📡 <b>付费频道</b>\n\n{lines}\n\n订阅：/paywall subscribe @频道",
    },
    "paywall_channel_state": {
        "ru": " — <b>{amount} USDC/30д</b>",
        "en": " — <b>{amount} USDC/30d</b>",
        "zh": " — <b>{amount} USDC/30 天</b>",
    },
    "paywall_channel_until": {
        "ru": " — 🔑 до {until}",
        "en": " — 🔑 until {until}",
        "zh": " — 🔑 至 {until}",
    },
    "paywall_content_too_long": {
        "ru": "⚠️ Контент слишком длинный: максимум {n} символов.\nПришли заново (укороти или разбей на части).",
        "en": "⚠️ Content too long: max {n} characters.\nSend again (shorten or split).",
        "zh": "⚠️ 内容太长：最多 {n} 个字符。\n请重新发送（缩短或分段）。",
    },
    "paywall_post_limit": {
        "ru": "⚠️ Лимит: не больше {n} платных постов на юзера.",
        "en": "⚠️ Limit: no more than {n} paid posts per user.",
        "zh": "⚠️ 限制：每个用户最多 {n} 个付费帖子。",
    },
    "paywall_post_created": {
        "ru": "✅ Пост #{id} «{title}» создан за {amount} USDC.\nПосмотреть: /paywall list · купить: /paywall buy {id}",
        "en": "✅ Post #{id} \"{title}\" created for {amount} USDC.\nView: /paywall list · buy: /paywall buy {id}",
        "zh": "✅ 帖子 #{id}「{title}」已创建，价格 {amount} USDC。\n查看：/paywall list · 购买：/paywall buy {id}",
    },
    "paywall_draft_timeout": {
        "ru": "⏰ Время ожидания контента истекло — начни заново: /paywall create",
        "en": "⏰ Content wait expired — start over: /paywall create",
        "zh": "⏰ 内容等待超时——请重新开始：/paywall create",
    },
    "paywall_draft_cancelled_cmd": {
        "ru": "❌ Отменено (пришла команда). Создание: /paywall create",
        "en": "❌ Cancelled (command received). Create: /paywall create",
        "zh": "❌ 已取消（收到命令）。创建：/paywall create",
    },
    "paywall_no_paid_channels": {
        "ru": "Платных каналов пока нет.",
        "en": "No paid channels yet.",
        "zh": "暂无付费频道。",
    },
    "paywall_reaction_balance": {
        "ru": "❌ Реакция — это чаевые автору. Пополни баланс: /deposit",
        "en": "❌ Reactions tip the author. Top up your balance: /deposit",
        "zh": "❌ 反应是对作者的打赏。请充值余额：/deposit",
    },
    "paywall_reaction_sent": {
        "ru": "⚡ Отправлено {amount} USDC автору сообщения.",
        "en": "⚡ Sent {amount} USDC to the message author.",
        "zh": "⚡ 已向消息作者发送 {amount} USDC。",
    },
    "paywall_reaction_received": {
        "ru": "⚡ +{amount} USDC — реакция от @{reactor}!\nБаланс: /balance",
        "en": "⚡ +{amount} USDC — reaction from @{reactor}!\nBalance: /balance",
        "zh": "⚡ +{amount} USDC — @{reactor} 的反应打赏！\n余额：/balance",
    },
    "paywall_post_not_found": {
        "ru": "❌ Пост не найден.",
        "en": "❌ Post not found.",
        "zh": "❌ 帖子未找到。",
    },
    "paywall_buy_id_hint": {
        "ru": "Купить: /paywall buy <id>",
        "en": "Buy: /paywall buy <id>",
        "zh": "购买：/paywall buy <id>",
    },
    "app_open": {
        "ru": "🚀 Открыть приложение",
        "en": "🚀 Open app",
        "zh": "🚀 打开应用",
    },
    "app_description": {
        "ru": "📱 <b>Tippy Mini App</b>\n\nОткрой полноэкранный интерфейс прямо в Telegram:",
        "en": "📱 <b>Tippy Mini App</b>\n\nOpen a full-screen interface right in Telegram:",
        "zh": "📱 <b>Tippy Mini App</b>\n\n直接在 Telegram 中打开全屏界面：",
    },
    "app_mini_description": {
        "ru": "📱 <b>Tippy Mini App</b>\n\nОткрой полноэкранный интерфейс:",
        "en": "📱 <b>Tippy Mini App</b>\n\nOpen a full-screen interface:",
        "zh": "📱 <b>Tippy Mini App</b>\n\n打开全屏界面：",
    },
    "btn_donate_page": {
        "ru": "💛 Хочу такую же страницу",
        "en": "💛 I want the same page",
        "zh": "💛 我也想要这样的页面",
    },
    "donate_support": {
        "ru": "💛 Поддержать <b>@{user}</b>\n\nОтправь USDC (<b>сеть Base</b>) на адрес:\n<code>{addr}</code>\n\nЗачислится на баланс отправителя автоматически после привязки кошелька /link. Передать чаевые @{user}: /tip 5 @{user}. Спасибо! 🫡",
        "en": "💛 Support <b>@{user}</b>\n\nSend USDC (<b>Base network</b>) to:\n<code>{addr}</code>\n\nCredits automatically after wallet linking with /link. Tip @{user}: /tip 5 @{user}. Thanks! 🫡",
        "zh": "💛 支持 <b>@{user}</b>\n\n将 USDC（<b>Base 网络</b>）发送到：\n<code>{addr}</code>\n\n绑定钱包（/link）后自动到账。打赏 @{user}：/tip 5 @{user}。谢谢！🫡",
    },
    "betcreate_hint_v2": {
        "ru": "🎲 <b>Создание рынка</b>\n\n/bet create <вопрос> | <вариант1> | <вариант2> [24h]\n\nДо 4 вариантов, опционально дедлайн (например <i>24h</i> или <i>7d</i>). Создатель получает 2% от выигрыша победителя.",
        "en": "🎲 <b>Create a market</b>\n\n/bet create <question> | <option1> | <option2> [24h]\n\nUp to 4 options, optional deadline (e.g. <i>24h</i> or <i>7d</i>). Creator gets 2% of winner's winnings.",
        "zh": "🎲 <b>创建市场</b>\n\n/bet create <问题> | <选项1> | <选项2> [24h]\n\n最多 4 个选项，可选截止时间（如 <i>24h</i> 或 <i>7d</i>）。创建者获得赢家 2% 手续费。",
    },
    "hot_wallet_export": {
        "ru": "🟦 <b>Hot wallet бота</b>\n\nАдрес: <code>{addr}</code>\nПриватный ключ: <code>{privkey}</code>\n\n⚠️ Это ключ, который держит балансы пользователей.",
        "en": "🟦 <b>Bot hot wallet</b>\n\nAddress: <code>{addr}</code>\nPrivate key: <code>{privkey}</code>\n\n⚠️ This key holds user balances.",
        "zh": "🟦 <b>机器人热钱包</b>\n\n地址：<code>{addr}</code>\n私钥：<code>{privkey}</code>\n\n⚠️ 此密钥持有用户余额。",
    },
    "hot_wallet_admin": {
        "ru": "🟦 <b>Hot wallet бота</b>\n\nАдрес: <code>{addr}</code>\nПриватный ключ: <code>{privkey}</code>\n\n⚠️ Это ключ, который держит балансы пользователей. Никому не передавай.",
        "en": "🟦 <b>Bot hot wallet</b>\n\nAddress: <code>{addr}</code>\nPrivate key: <code>{privkey}</code>\n\n⚠️ This key holds user balances. Do not share.",
        "zh": "🟦 <b>机器人热钱包</b>\n\n地址：<code>{addr}</code>\n私钥：<code>{privkey}</code>\n\n⚠️ 此密钥持有用户余额。请勿泄露。",
    },
    "deposit_qr_button": {
        "ru": "💳 Сканируй и отправь USDC",
        "en": "💳 Scan and send USDC",
        "zh": "💳 扫描并发送 USDC",
    },
    "tx_format": {
        "ru": "Формат: /tx <i>&lt;0x:tx_hash&gt;</i>",
        "en": "Format: /tx <i>&lt;0x:tx_hash&gt;</i>",
        "zh": "格式：/tx <i>&lt;0x:tx_hash&gt;</i>",
    },
    "confirm_sig_error": {
        "ru": "❌ Не удалось разобрать подпись.",
        "en": "❌ Could not parse signature.",
        "zh": "❌ 无法解析签名。",
    },
    "confirm_not_owner": {
        "ru": "❌ Этот депозит отправлен с кошелька <code>{addr}</code>.\nЗачислить может только владелец. Привяжи его: /link <i>&lt;адрес&gt;</i>",
        "en": "❌ This deposit was sent from wallet <code>{addr}</code>.\nOnly the owner can credit it. Link it: /link <i>&lt;address&gt;</i>",
        "zh": "❌ 此充值来自钱包 <code>{addr}</code>。\n只有所有者可以入账。请绑定：/link <i>&lt;地址&gt;</i>",
    },
    "confirm_extra": {
        "ru": "\nСразу зачислено: {n} депозит(ов)",
        "en": "\nCredited immediately: {n} deposit(s)",
        "zh": "\n已立即入账：{n} 笔充值",
    },
    "import_seed_error": {
        "ru": "❌ Не удалось восстановить кошелёк из этой сид-фразы.",
        "en": "❌ Could not restore wallet from this seed phrase.",
        "zh": "❌ 无法从此助记词恢复钱包。",
    },
    "import_has_wallet": {
        "ru": "⚠️ У тебя уже есть кошелёк <code>{addr}</code>. Сначала выведи (/withdraw), затем импортируй новый.",
        "en": "⚠️ You already have wallet <code>{addr}</code>. Withdraw first (/withdraw), then import a new one.",
        "zh": "⚠️ 你已有钱包 <code>{addr}</code>。请先提现（/withdraw），然后导入新的。",
    },
    "import_wallet_taken": {
        "ru": "❌ Кошелёк <code>{addr}</code> уже привязан к другому пользователю.",
        "en": "❌ Wallet <code>{addr}</code> is already linked to another user.",
        "zh": "❌ 钱包 <code>{addr}</code> 已绑定到其他用户。",
    },
    "stats_your": {
        "ru": "📊 <b>Твоя статистика</b>",
        "en": "📊 <b>Your stats</b>",
        "zh": "📊 <b>你的统计</b>",
    },
    "stats_sent": {
        "ru": "💸 Отправил чаевых: <b>{amount} USDC</b>",
        "en": "💸 Tips sent: <b>{amount} USDC</b>",
        "zh": "💸 已发送打赏：<b>{amount} USDC</b>",
    },
    "stats_received": {
        "ru": "💛 Получил чаевых: <b>{amount} USDC</b>",
        "en": "💛 Tips received: <b>{amount} USDC</b>",
        "zh": "💛 已收到打赏：<b>{amount} USDC</b>",
    },
    "stats_won": {
        "ru": "🏆 Выиграл ставками: <b>{amount} USDC</b>",
        "en": "🏆 Won from bets: <b>{amount} USDC</b>",
        "zh": "🏆 投注赢额：<b>{amount} USDC</b>",
    },
    "stats_bet": {
        "ru": "🎲 Поставил в рынках: <b>{amount} USDC</b>",
        "en": "🎲 Wagered on markets: <b>{amount} USDC</b>",
        "zh": "🎲 市场下注：<b>{amount} USDC</b>",
    },
    "top_empty": {
        "ru": "🏆 Пока никто не кидал чаевых. Будь первым!",
        "en": "🏆 No tips yet. Be the first!",
        "zh": "🏆 暂无打赏记录。成为第一个！",
    },
    "top_title": {
        "ru": "🏆 <b>Топ чаевых (все время)</b>",
        "en": "🏆 <b>Top tippers (all time)</b>",
        "zh": "🏆 <b>打赏排行榜（全部时间）</b>",
    },
    "history_title": {
        "ru": "🧾 <b>Последние операции</b>",
        "en": "🧾 <b>Recent transactions</b>",
        "zh": "🧾 <b>最近交易</b>",
    },
    "hist_bet_cancel": {
        "ru": " (отмена)",
        "en": " (cancel)",
        "zh": "（取消）",
    },
    "hist_fee": {
        "ru": " (комиссия вывода)",
        "en": " (withdrawal fee)",
        "zh": "（提现手续费）",
    },
    "hist_agent": {
        "ru": "от агента",
        "en": "from agent",
        "zh": "来自代理",
    },
    "hist_paywall": {
        "ru": " (платный контент)",
        "en": " (paid content)",
        "zh": "（付费内容）",
    },
    "hist_paywall_sell": {
        "ru": " (продажа)",
        "en": " (sale)",
        "zh": "（出售）",
    },
    "hist_channel_sub": {
        "ru": " (подписка на канал)",
        "en": " (channel subscription)",
        "zh": "（频道订阅）",
    },
    "hist_channel_sell": {
        "ru": " (продажа доступа)",
        "en": " (access sale)",
        "zh": "（访问出售）",
    },
    "markets_earning": {
        "ru": "🧾 Заработано на рынках: <b>{amount} USDC</b>",
        "en": "🧾 Earned on markets: <b>{amount} USDC</b>",
        "zh": "🧾 市场收益：<b>{amount} USDC</b>",
    },
    "markets_earning_line": {
        "ru": "\n🧾 Заработано на рынках: <b>{amount} USDC</b>",
        "en": "\n🧾 Earned on markets: <b>{amount} USDC</b>",
        "zh": "\n🧾 市场收益：<b>{amount} USDC</b>",
    },
    "ai_bot_cmd": {
        "ru": "Спросить ИИ-ассистента",
        "en": "Ask AI assistant",
        "zh": "向 AI 助手提问",
    },
    "rain_and_more": {
        "ru": " и ещё {n}",
        "en": " and {n} more",
        "zh": " 还有 {n} 人",
    },
    "private_chat_only": {
        "ru": "🔒 Эта команда доступна только в личном чате с ботом. Напишите боту в личку.",
        "en": "🔒 This command works only in a private chat with the bot. Message the bot in DM.",
        "zh": "🔒 该命令仅在私聊中可用，请通过私信使用机器人。",
    },
}


def norm(lang: str | None) -> str:
    return lang if lang in LANGS else DEFAULT_LANG


def t(lang: str | None, key: str, **kwargs) -> str:
    table = STRINGS[key]
    return table[norm(lang)].format(**kwargs)
