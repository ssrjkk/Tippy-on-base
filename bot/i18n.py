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
    },
    "about_body": {
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
}


def norm(lang: str | None) -> str:
    return lang if lang in LANGS else DEFAULT_LANG


def t(lang: str | None, key: str, **kwargs) -> str:
    table = STRINGS[key]
    return table[norm(lang)].format(**kwargs)
