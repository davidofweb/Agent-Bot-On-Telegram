import { Telegraf } from 'telegraf';
import OpenAI from 'openai';
import { CONFIG } from './config.js';
import { loadMemory, saveMemory, getChatState, mergeFiles } from './memory.js';
import { pushToGitHubIfConfigured, deployToVercel } from './deployer.js';

const bot = new Telegraf(CONFIG.BOT_TOKEN);
const agentRouter = new OpenAI({
  baseURL: 'https://agentrouter.org/v1',
  apiKey: CONFIG.AGENTROUTER_KEY,
});

const activeLocks = new Map();
let availableModels = [CONFIG.PRIMARY_MODEL, CONFIG.FALLBACK_MODEL, 'gpt-6.1-sol', 'deepseek-v4-flash'];
let lastModelSync = null;

// --- Auto-Sync Models ---
async function syncModelsFromRouter() {
  try {
    const res = await fetch('https://agentrouter.org/v1/models', {
      headers: { Authorization: `Bearer ${CONFIG.AGENTROUTER_KEY}` },
    });
    if (!res.ok) return availableModels;
    const data = await res.json();
    const ids = Array.isArray(data?.data) ? data.data.map((m) => m.id).filter(Boolean) : [];
    if (ids.length > 0) {
      const prioritized = [CONFIG.PRIMARY_MODEL, CONFIG.FALLBACK_MODEL].filter((m) => ids.includes(m));
      const others = ids.filter((m) => !prioritized.includes(m));
      availableModels = [...prioritized, ...others];
      lastModelSync = new Date().toISOString();
    }
  } catch {}
  return availableModels;
}

function buildModelChain(preferred) {
  return [...new Set([preferred || CONFIG.PRIMARY_MODEL, CONFIG.PRIMARY_MODEL, CONFIG.FALLBACK_MODEL])].filter(Boolean);
}

// --- Payload Extraction & Repair ---
function sanitizeFilesArray(files) {
  if (!Array.isArray(files)) return [];
  return files
    .filter((f) => f && typeof f.file === 'string' && typeof f.data === 'string')
    .map((f) => ({ file: f.file.replace(/^\/+/, '').trim(), data: f.data }))
    .filter((f) => f.file.length > 0 && f.data.trim().length > 0);
}

function parseProjectPayload(rawText) {
  const cleaned = rawText.trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '').trim();
  const parsed = JSON.parse(cleaned);
  const files = sanitizeFilesArray(parsed.files);
  if (files.length === 0) throw new Error('No valid files returned.');
  
  const baseName = String(parsed.projectName || 'web-app')
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, '-')
    .slice(0, 36) || 'web-app';

  return { projectName: baseName, summary: String(parsed.summary || 'Updated files').slice(0, 200), files };
}

async function repairInvalidJson(model, brokenOutput) {
  const completion = await agentRouter.chat.completions.create({
    model,
    temperature: 0.1,
    messages: [
      {
        role: 'system',
        content: `Fix the malformed output into STRICT valid JSON matching:
{"projectName":"kebab-case-name","summary":"short note","files":[{"file":"index.html","data":"..."}]}
Escape all newlines and quotes inside strings. Return ONLY raw JSON.`,
      },
      { role: 'user', content: brokenOutput.slice(0, 24000) },
    ],
  });
  return parseProjectPayload(completion.choices[0].message.content);
}

// --- Generator with Fallback Loop ---
async function generateProject(chatState, userPrompt) {
  await syncModelsFromRouter();
  const candidates = buildModelChain(chatState.preferredModel);
  const hasExisting = Array.isArray(chatState.files) && chatState.files.length > 0;
  
  const systemPrompt = `You are an expert full-stack engineer.
Break code into clean multi-file structures (e.g. index.html, styles.css, app.js, or modular server scripts).
Return ONLY raw valid JSON matching:
{
  "projectName": "short-kebab-case-name",
  "summary": "brief description of additions/repairs",
  "files": [ { "file": "path/filename.ext", "data": "file contents string" } ]
}
Rules:
- On existing project edits, return ONLY the files that need creating or updating in "files" so unchanged files remain intact.`;

  const messages = [{ role: 'system', content: systemPrompt }];
  if (hasExisting) {
    messages.push({
      role: 'user',
      content: `Existing files:\n${JSON.stringify({ projectName: chatState.projectName, files: chatState.files }).slice(0, 28000)}`,
    });
  }
  messages.push({ role: 'user', content: userPrompt });

  let lastErr = null;
  for (const model of candidates) {
    for (let attempt = 1; attempt <= 2; attempt++) {
      try {
        const completion = await agentRouter.chat.completions.create({
          model,
          temperature: attempt === 1 ? 0.3 : 0.15,
          messages,
        });
        const content = completion.choices[0]?.message?.content || '';
        try {
          const payload = parseProjectPayload(content);
          return { ...payload, modelUsed: model };
        } catch {
          const repaired = await repairInvalidJson(model, content);
          return { ...repaired, modelUsed: `${model} (repaired)` };
        }
      } catch (err) {
        lastErr = err;
      }
    }
  }
  throw new Error(`All models failed (${candidates.join(' -> ')}). Last error: ${lastErr?.message || 'Unknown'}`);
}

// --- Middleware & Concurrency Lock ---
bot.use((ctx, next) => {
  if (CONFIG.ALLOWED_USER && String(ctx.from?.id) !== CONFIG.ALLOWED_USER) {
    return ctx.reply('Unauthorized user.');
  }
  return next();
});

async function withLock(chatId, fn) {
  const key = String(chatId);
  if (activeLocks.get(key)) throw new Error('A build or deploy is already running. Please wait.');
  activeLocks.set(key, true);
  try { return await fn(); } finally { activeLocks.delete(key); }
}

// --- Telegram Commands ---
bot.start((ctx) => ctx.reply(`Ready!\nPrimary: ${CONFIG.PRIMARY_MODEL} | Fallback: ${CONFIG.FALLBACK_MODEL}\nCommands: /status, /models, /files, /file <path>, /deploy, /new, /help`));

bot.command('help', (ctx) => ctx.reply(`/status - View project status\n/models - Manage models\n/files - List files\n/file <path> - Inspect a specific file\n/deploy - Force deploy\n/new - Reset project memory`));

bot.command('status', async (ctx) => {
  const mem = await loadMemory();
  const state = getChatState(mem, ctx.chat.id);
  ctx.reply(`Model: ${state.preferredModel}\nProject: ${state.projectName || 'None'}\nSaved Files: ${state.files.length}\nLast URL: ${state.lastUrl ? `https://${state.lastUrl}` : 'None'}`);
});

bot.command('models', async (ctx) => {
  const arg = ctx.message.text.replace(/^\/models\s*/i, '').trim();
  const models = await syncModelsFromRouter();
  const mem = await loadMemory();
  const state = getChatState(mem, ctx.chat.id);

  if (arg) {
    state.preferredModel = arg;
    await saveMemory(mem);
    return ctx.reply(`Preferred model set to: ${arg}`);
  }
  ctx.reply(`Active: ${state.preferredModel}\nAvailable:\n${models.slice(0, 10).map((m) => `• ${m}`).join('\n')}\nUse /models <model-id> to switch.`);
});

bot.command('files', async (ctx) => {
  const mem = await loadMemory();
  const state = getChatState(mem, ctx.chat.id);
  if (!state.files.length) return ctx.reply('No saved files.');
  ctx.reply(`Files (${state.projectName}):\n${state.files.map((f) => `• ${f.file} (${Buffer.byteLength(f.data)} B)`).join('\n')}`);
});

bot.command('file', async (ctx) => {
  const target = ctx.message.text.replace(/^\/file\s*/i, '').trim().replace(/^\/+/, '');
  const mem = await loadMemory();
  const state = getChatState(mem, ctx.chat.id);
  const found = state.files.find((f) => f.file === target);
  if (!found) return ctx.reply(`File "${target}" not found. Available: ${state.files.map((f) => f.file).join(', ') || 'none'}`);
  ctx.reply(`File: ${found.file}\n\n${found.data.length > 3000 ? `${found.data.slice(0, 3000)}\n... (truncated)` : found.data}`);
});

bot.command('new', async (ctx) => {
  const mem = await loadMemory();
  mem.chats[String(ctx.chat.id)] = { preferredModel: CONFIG.PRIMARY_MODEL, projectName: null, lastUrl: null, repoUrl: null, files: [], history: [] };
  await saveMemory(mem);
  ctx.reply('Memory cleared. Ready for a new project.');
});

bot.command('deploy', async (ctx) => {
  try {
    await withLock(ctx.chat.id, async () => {
      const mem = await loadMemory();
      const state = getChatState(mem, ctx.chat.id);
      if (!state.files.length) return ctx.reply('No saved files to deploy.');
      const statusMsg = await ctx.reply(`Deploying "${state.projectName}"...`);
      let repoUrl = state.repoUrl;
      if (CONFIG.GITHUB_TOKEN && CONFIG.GITHUB_OWNER && state.files.length > 1) {
        repoUrl = await pushToGitHubIfConfigured(state.projectName, state.files, 'Manual deploy command');
        state.repoUrl = repoUrl;
      }
      const liveUrl = await deployToVercel(state.projectName, state.files, repoUrl);
      state.lastUrl = liveUrl;
      await saveMemory(mem);
      await ctx.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, undefined, `Deployed!\nLive URL: https://${liveUrl}${repoUrl ? `\nRepo: ${repoUrl}` : ''}`);
    });
  } catch (err) { ctx.reply(`Deploy error: ${err.message}`); }
});

// --- Main Text Handler ---
bot.on('text', async (ctx) => {
  const text = ctx.message.text.trim();
  if (!text || text.startsWith('/')) return;

  try {
    await withLock(ctx.chat.id, async () => {
      const mem = await loadMemory();
      const state = getChatState(mem, ctx.chat.id);
      const isEdit = state.files.length > 0;
      const statusMsg = await ctx.reply(isEdit ? `Updating "${state.projectName}"...` : `Generating project...`);

      const generated = await generateProject(state, text);
      const projectName = isEdit && state.projectName ? state.projectName : generated.projectName;

      state.projectName = projectName;
      state.files = isEdit ? mergeFiles(state.files, generated.files) : generated.files;
      await saveMemory(mem);

      let repoUrl = state.repoUrl;
      if (CONFIG.GITHUB_TOKEN && CONFIG.GITHUB_OWNER && state.files.length > 1) {
        await ctx.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, undefined, `Code updated (${generated.modelUsed}). Syncing to GitHub...`);
        repoUrl = await pushToGitHubIfConfigured(projectName, state.files, generated.summary);
        state.repoUrl = repoUrl;
        await saveMemory(mem);
      }

      await ctx.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, undefined, `Deploying to Vercel (${generated.modelUsed})...`);
      const liveUrl = await deployToVercel(projectName, state.files, repoUrl);
      state.lastUrl = liveUrl;
      await saveMemory(mem);

      await ctx.telegram.editMessageText(
        ctx.chat.id,
        statusMsg.message_id,
        undefined,
        `${isEdit ? 'Updated & redeployed' : 'Deployed'} (${generated.modelUsed})\nProject: ${projectName}\nSummary: ${generated.summary}\nLive URL: https://${liveUrl}${repoUrl ? `\nRepo: ${repoUrl}` : ''}`
      );
    });
  } catch (err) { ctx.reply(`Error: ${err.message}`); }
});

bot.launch();
console.log('🤖 Telegram AgentRouter bot running...');
