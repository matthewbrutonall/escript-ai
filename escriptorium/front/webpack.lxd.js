/**
 * Production build with unhashed filenames matching the official
 * eScriptorium image's /static/main.js, /static/imagesPage.js, etc.
 */
const path = require("path");
const MiniCssExtractPlugin = require("mini-css-extract-plugin");
const { merge } = require("webpack-merge");
const common = require("./webpack.common.js");

const config = merge(common, {
    mode: "production",
    output: {
        filename: "[name].js",
        chunkFilename: "[name].chunk.js",
        path: path.resolve(__dirname, "./dist-lxd/"),
        publicPath: "/static/",
    },
});

config.plugins = config.plugins.map((plugin) => {
    if (plugin instanceof MiniCssExtractPlugin) {
        return new MiniCssExtractPlugin({
            filename: "[name].css",
            chunkFilename: "[name].chunk.css",
        });
    }
    return plugin;
});

module.exports = config;
