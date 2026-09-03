const app = document.querySelector<HTMLElement>("#app");

if (!app) throw new Error("Missing app root");

if (window.location.pathname === "/studio" || window.location.pathname.startsWith("/studio/")) {
  const { mountCharacterRobotStudio } = await import("./studio/main");
  await mountCharacterRobotStudio(app);
} else {
  await import("./main");
}
