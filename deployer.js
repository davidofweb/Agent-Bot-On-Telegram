import { CONFIG } from './config.js';

export async function pushToGitHubIfConfigured(repoName, files, commitMessage) {
  if (!CONFIG.GITHUB_TOKEN || !CONFIG.GITHUB_OWNER) return null;

  const headers = {
    Authorization: `Bearer ${CONFIG.GITHUB_TOKEN}`,
    Accept: 'application/vnd.github+json',
    'Content-Type': 'application/json',
  };

  // Ensure repo exists
  let repoRes = await fetch(`https://api.github.com/repos/${CONFIG.GITHUB_OWNER}/${repoName}`, { headers });
  if (repoRes.status === 404) {
    repoRes = await fetch('https://api.github.com/user/repos', {
      method: 'POST',
      headers,
      body: JSON.stringify({ name: repoName, private: true, auto_init: true }),
    });
    if (!repoRes.ok) {
      const err = await repoRes.json().catch(() => ({}));
      throw new Error(`GitHub repo creation failed: ${err.message || repoRes.status}`);
    }
  }

  // Push individual files
  for (const f of files) {
    const filePath = f.file;
    let sha = undefined;
    const existingRes = await fetch(
      `https://api.github.com/repos/${CONFIG.GITHUB_OWNER}/${repoName}/contents/${encodeURIComponent(filePath)}`,
      { headers }
    );
    if (existingRes.ok) {
      const existingData = await existingRes.json();
      sha = existingData.sha;
    }

    const putRes = await fetch(
      `https://api.github.com/repos/${CONFIG.GITHUB_OWNER}/${repoName}/contents/${encodeURIComponent(filePath)}`,
      {
        method: 'PUT',
        headers,
        body: JSON.stringify({
          message: commitMessage || `Update ${filePath}`,
          content: Buffer.from(f.data, 'utf8').toString('base64'),
          ...(sha ? { sha } : {}),
        }),
      }
    );

    if (!putRes.ok) {
      const err = await putRes.json().catch(() => ({}));
      throw new Error(`GitHub push failed for ${filePath}: ${err.message || putRes.status}`);
    }
  }

  return `https://github.com/${CONFIG.GITHUB_OWNER}/${repoName}`;
}

export async function deployToVercel(projectName, files, repoUrl = null) {
  const deploymentName = projectName.toLowerCase().replace(/[^a-z0-9-]/g, '-').slice(0, 50);

  const body = repoUrl && CONFIG.GITHUB_OWNER
    ? {
        name: deploymentName,
        target: 'production',
        gitSource: {
          type: 'github',
          repo: `${CONFIG.GITHUB_OWNER}/${deploymentName}`,
          ref: 'main',
        },
      }
    : {
        name: deploymentName,
        target: 'production',
        files,
        projectSettings: { framework: null },
      };

  const res = await fetch('https://api.vercel.com/v13/deployments', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${CONFIG.VERCEL_TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error?.message || 'Vercel deployment failed');
  }
  return data.url;
}
