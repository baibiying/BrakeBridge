import { existsSync } from 'node:fs'
import { createInterface } from 'node:readline'
import { chromium } from 'playwright'

const adminOrigin = 'https://admin.hqsmartcloud.com'
const figeonHost = 'figeon.hqsmartcloud.com'
const send = value => process.stdout.write(JSON.stringify(value) + '\n')
const chrome = process.env.SOCIAL_FIGEON_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const browser = await chromium.launch({ headless: true, timeout: 12000, ...(existsSync(chrome) ? { executablePath: chrome } : {}) })
const cli = createInterface({ input: process.stdin, crlfDelay: Infinity })

async function showChallenge(page, message) {
  const handle = await page.waitForFunction(() => {
    const input = [...document.querySelectorAll('input')].find(item => item.placeholder === '请输入验证码')
    if (!input?.getClientRects().length) return false
    for (let parent = input?.parentElement, level = 0; parent && level < 4; parent = parent.parentElement, level++) {
      const matches = [...parent.querySelectorAll('img,canvas')].filter(el => {
        const { width, height } = el.getBoundingClientRect()
        return width >= 45 && width <= 250 && height >= 20 && height <= 80
      })
      if (matches.length === 1) {
        const image = matches[0]
        if (image.tagName === 'IMG' && (!image.complete || !image.naturalWidth)) return false
        const { x, y, width, height } = image.getBoundingClientRect()
        return { x, y, width, height }
      }
    }
    return false
  }, undefined, { timeout: 26000 })
  const snapshot = await page.screenshot({ clip: await handle.jsonValue(), timeout: 7500 })
  send({ state: 'challenge', message, image: snapshot.toString('base64') })
}

try {
  const context = await browser.newContext({ locale: 'zh-CN', acceptDownloads: false })
  let receiveHandoff
  const handoff = new Promise(resolve => { receiveHandoff = resolve })
  await context.route(url => url.hostname === figeonHost ||
    (url.protocol === 'http:' && url.hostname === new URL(adminOrigin).hostname), async route => {
    const url = new URL(route.request().url())
    if (url.hostname === figeonHost && route.request().isNavigationRequest() && url.pathname === '/') {
      const token = url.searchParams.get('token') || ''
      if (/^[A-Za-z0-9._~+/-]{16,1024}={0,2}$/.test(token)) receiveHandoff(token)
    }
    await route.abort('blockedbyclient')
  })
  const page = await context.newPage()
  await page.goto(adminOrigin + '/#/login', { waitUntil: 'commit', timeout: 17000 })
  await showChallenge(page, '请由管理员输入当前图中的字符。')

  for await (const line of cli) {
    const { code } = JSON.parse(line)
    if (new URL(page.url()).origin !== adminOrigin) break
    await page.getByPlaceholder('邮箱/手机号').fill(process.env.SOCIAL_FIGEON_USER)
    await page.getByPlaceholder('请输入密码').fill(process.env.SOCIAL_FIGEON_PASSWORD)
    await page.getByPlaceholder('请输入验证码').fill(code)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await Promise.race([
      page.getByRole('menuitem', { name: '飞鸽营销', exact: true }).waitFor({ timeout: 8500 }),
      page.getByText(/验证码错误|验证码已过期|密码错误|账号错误/).first().waitFor({ timeout: 8500 }),
    ]).catch(() => {})
    if (new URL(page.url()).hash.startsWith('#/login')) {
      if (await page.getByText(/密码错误|账号错误/).first().isVisible()) {
        send({ state: 'unavailable', message: '转单云帐号或密码需要管理员核对。' })
        break
      }
      await page.reload({ waitUntil: 'domcontentloaded', timeout: 11000 })
      await showChallenge(page, '登录未通过，请输入这张新图中的字符。')
      continue
    }

    await page.getByRole('menuitem', { name: '飞鸽营销', exact: true }).click({ timeout: 6500 })
    let timer
    const token = await Promise.race([handoff, new Promise(resolve => { timer = setTimeout(resolve, 8500) })])
    clearTimeout(timer)
    if (token) {
      send({ state: 'authorized', token })
    } else {
      send({ state: 'unavailable', message: '转单云登录已通过，飞鸽未返回本次授权。请核对该帐号的飞鸽权限。' })
    }
    break
  }
} catch {
  send({ state: 'unavailable', message: '暂时无法连接转单云验证页，请重新打开。' })
} finally {
  cli.close()
  await browser.close()
}
