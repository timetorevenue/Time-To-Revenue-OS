// Shared navigation for the existing Stage 1/2 browser proofs after the workspace UX overhaul.
export async function openStudioTool(page, id) {
 if (!['inspect', 'manager', 'code', 'build'].includes(id)) await page.locator('.live-more > summary').click()
 await page.locator(`button[data-tab="${id}"]`).click()
 await page.waitForTimeout(300)
}

export async function openContentTool(page, label) {
 const button = page.getByRole('button', {name: `Open ${label}`, exact: true})
 if (!(await page.locator('.live-left').isVisible()) || (await button.getAttribute('aria-pressed')) !== 'true') await button.click()
 await page.waitForTimeout(300)
}
