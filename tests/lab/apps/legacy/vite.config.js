import { defineConfig } from 'vite';
import { writeFileSync, mkdirSync } from 'node:fs';
export default defineConfig({build:{outDir:'public/build',emptyOutDir:true,rollupOptions:{input:'resources/js/app.js',output:{entryFileNames:'app.js'}}},plugins:[{name:'paddock-node-version',closeBundle(){mkdirSync('public/build',{recursive:true});writeFileSync('public/build/node-version.json',JSON.stringify({node:process.version})+'\n')}}]});
