const d = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
console.log(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`);
