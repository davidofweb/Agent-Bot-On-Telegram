export const CONFIG = {
  BOT_TOKEN: process.env.TELEGRAM_BOT_TOKEN,
  ALLOWED_USER: String(process.env.ALLOWED_TELEGRAM_USER_ID || ''),
  AGENTROUTER_KEY: process.env.AGENTROUTER_API_KEY,
  VERCEL_TOKEN: process.env.VERCEL_TOKEN,
  GITHUB_TOKEN: process.env.GITHUB_TOKEN || '',
  GITHUB_OWNER: process.env.GITHUB_OWNER || '',
  PRIMARY_MODEL: 'gpt-6.2-astra',
  FALLBACK_MODEL: 'gpt-6.2-sol',
  MEMORY_FILE: 'bot_memory.json',
};

if (!CONFIG.BOT_TOKEN || !CONFIG.AGENTROUTER_KEY || !CONFIG.VERCEL_TOKEN) {
  console.error('❌ Missing required env variables: TELEGRAM_BOT_TOKEN, AGENTROUTER_API_KEY, VERCEL_TOKEN');
  process.exit(1);
}
