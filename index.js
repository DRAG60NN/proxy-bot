const { Telegraf } = require('telegraf');

const BOT_TOKEN = process.env.BOT_TOKEN; // Токен зададим через Render

const bot = new Telegraf(BOT_TOKEN);

bot.start((ctx) => {
  const code = ctx.startPayload || 'неизвестен';

  ctx.reply(`🔎 Для кода ${code} нажмите кнопку ниже, чтобы открыть WebApp`, {
    reply_markup: {
      inline_keyboard: [[
        {
          text: "Открыть WebApp",
          web_app: {
            url: "https://drag60nn.github.io/fetch.html"
          }
        }
      ]]
    }
  });
});

bot.launch();
console.log("Bot запущен");

// Для корректной остановки в Render
process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
