// Exercise the visible section navigation; older fixtures use the original sidebar.
const sections={overview:'overview',attention:'overview',board:'work',episodes:'work',pending:'work',map:'work',decisions:'decisions',drift:'decisions',documents:'knowledge',sources:'knowledge',direction:'knowledge',research:'knowledge',corrections:'knowledge',lessons:'knowledge',patterns:'knowledge',skills:'knowledge',events:'activity',captures:'activity'};
async function navigate(page,view,click=async locator=>locator.click()) {
  const section=page.locator('[data-section='+sections[view]+']');
  if(await section.count() && (!await page.locator('[data-view='+view+']').isVisible() || await page.locator('body.nav-open').count())) {
    if(!await section.isVisible()) await click(page.locator('#menu-toggle'));
    await click(section);
  }
  await click(page.locator('[data-view='+view+']'));
}
module.exports={navigate};
