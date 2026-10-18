// SSE 流式客户端（文档 3 第 9.3 节）。
// 按 \n\n 切分事件帧（Phase 2 裁决：后端适配前端契约）。

export async function sendMessageStream({
  characterId, sessionId, message, onToken, onDone, onError,
}) {
  const token = Alpine.store('app').token;

  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
        'X-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
          || 'Asia/Shanghai',
      },
      body: JSON.stringify({
        character_id: characterId,
        session_id: sessionId,
        message,
      }),
    });

    if (!response.ok) {
      throw new Error('请求失败');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, {stream: true});
      const lines = buffer.split('\n\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.trim()) continue;
        const eventMatch = line.match(/^event: (.+)$/m);
        const dataMatch = line.match(/^data: (.+)$/m);
        if (!eventMatch || !dataMatch) continue;

        const event = eventMatch[1];
        const data = dataMatch[1];

        if (event === 'token') {
          onToken(data);
        } else if (event === 'done') {
          onDone(JSON.parse(data));
        } else if (event === 'error') {
          onError(new Error(JSON.parse(data).message));
        }
      }
    }
  } catch (error) {
    onError(error);
  }
}
