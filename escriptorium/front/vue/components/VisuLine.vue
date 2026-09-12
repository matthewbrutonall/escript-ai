<template>
    <g
        @mouseover="showOverlay"
        @mouseleave="hideOverlay"
        @click="edit"
    >
        <polygon
            :fill="(legacyModeEnabled && globalConfidenceVisible) || confidenceVizOn ? maskFillColor : 'transparent'"
            :stroke="maskStrokeColor"
            :points="maskPoints"
        />
        <path
            :id="textPathId"
            ref="pathElement"
            fill="none"
            :stroke="pathStrokeColor"
            :d="baselinePoints"
        />

        <text
            v-if="$store.state.document.mainTextDirection != 'ttb'"
            ref="textElement"
            :text-anchor="$store.state.document.defaultTextDirection == 'rtl' ? 'end' : ''"
            data-toggle="tooltip"
        >
            <textPath
                v-if="line.currentTrans"
                :href="'#' + textPathId"
            >
                {{ line.currentTrans.content }}
            </textPath>
        </text>

        <text
            v-else
            ref="textElement"
            :text-anchor="$store.state.document.defaultTextDirection == 'rtl' ? 'end' : ''"
            writing-mode="tb"
            font-size="1em"
            data-toggle="tooltip"
        >
            <textPath
                v-if="line.currentTrans"
                :href="'#' + textPathId"
            >
                {{ line.currentTrans.content }}
            </textPath>
        </text>
    </g>
</template>

<script>
import { mapState } from "vuex";
import { LineBase } from "../../src/editor/mixins.js";
import {
    displayedLineHeight,
    shouldShrinkToPath,
    visualFontSize,
} from "./visualText";

export default Vue.extend({
    mixins: [LineBase],
    props: {
        /**
         * Whether or not legacy mode is enabled by the user.
         */
        legacyModeEnabled: {
            type: Boolean,
            required: true,
        },
    },
    computed: {
        ...mapState({
            activeTool: (state) => state.globalTools.activeTool,
            globalConfidenceVisible: (state) => state.document.confidenceVisible,
            confidenceVizOn: (state) => state.document.confidenceVizOn,
        }),
        textPathId() {
            return this.line ? "textPath"+this.line.pk : "";
        },
        maskStrokeColor() {
            if (this.line.currentTrans && this.line.currentTrans.content) {
                return "none";
            } else {
                return "lightgrey";
            }
        },
        lineAvgConfidence() {
            // compute the average confidence for the current line
            if (this.line.currentTrans.avg_confidence) {
                return this.line.currentTrans.avg_confidence;
            } else if (this.line.currentTrans?.graphs?.length) {
                const lineConfidences = this.line.currentTrans.graphs.map((g) => g.confidence);
                return lineConfidences.reduce((all, one, _, src) => all += one / src.length, 0);
            }
            return null;
        },
        maskFillColor() {
            if (this.line.currentTrans?.graphs?.length || this.line.currentTrans?.avg_confidence) {
                // convert the avg confidence to hue (0 = red, 120 = green)
                // use a slight curve so that values are more easily red/yellow
                const hue = Math.pow(this.lineAvgConfidence, this.$store.state.document.confidenceScale) * 120;
                return `hsl(${hue}, 100%, 50%, 50%)`;
            }
            return "transparent";
        },
        maskPoints() {
            if (this.line == null || !this.line.mask) return "";
            return this.line.mask.map((pt) => Math.round(pt[0]*this.ratio)+","+Math.round(pt[1]*this.ratio)).join(" ");
        },
        fakeBaseline() {
            // create a fake path based on the mask,
            var min = this.line.mask.reduce((minPt, curPt) => (curPt[0] < minPt[0]) ? curPt : minPt);
            var max = this.line.mask.reduce((maxPt, curPt) => (curPt[0] > maxPt[0]) ? curPt : maxPt);
            return [min, max];
        },
        pathStrokeColor() {
            if (this.line.currentTrans && this.line.currentTrans.content) {
                return "none";
            } else {
                return "blue";
            }
        },
        baselinePoints() {
            var baseline, ratio = this.ratio;
            function ptToStr(pt) {
                return Math.round(pt[0]*ratio)+" "+Math.round(pt[1]*ratio);
            }
            if (this.line == null || this.line.baseline === null) {
                baseline = this.fakeBaseline;
            } else {
                baseline = this.line.baseline
            }
            return "M "+baseline.map((pt) => ptToStr(pt)).join(" L ");
        },
    },
    watch: {
        "line.currentTrans.content": function(n, o) {
            this.$nextTick(this.reset);
        },
        "line.baseline": function(n, o) {
            this.$nextTick(this.reset);
        }
    },
    mounted() {
        this.$nextTick(this.reset);
    },
    methods: {
        computeLineHeight() {
            if (!this.$refs.textElement) return;
            const height = displayedLineHeight(
                this.line && this.line.mask,
                this.ratio,
            );
            const size = visualFontSize(height, this.$parent.fontSizeRatio);
            this.$refs.textElement.setAttribute("font-size", `${size}px`);
        },
        computeTextLength() {
            const el = this.$refs.textElement;
            const path = this.$refs.pathElement;
            if (!el || !path || !this.line.currentTrans) return;
            el.removeAttribute("textLength");
            el.removeAttribute("lengthAdjust");
            if (!this.line.currentTrans.content) return;
            const natural = el.getComputedTextLength();
            const pathLength = path.getTotalLength();
            // Never stretch short strings (titles) across the baseline.
            // Only shrink when the natural width overflows the path.
            if (shouldShrinkToPath(natural, pathLength)) {
                el.setAttribute("textLength", `${pathLength}px`);
                el.setAttribute("lengthAdjust", "spacing");
            }
        },
        computeConfidence() {
            // compute the average confidence for this line
            if (this.line.currentTrans?.graphs?.length || this.line.currentTrans?.avg_confidence) {
                const confidence =  `Confidence: ${(this.lineAvgConfidence * 100).toFixed(1)}%`;
                // add confidence to bootstrap title related attributes
                this.$refs.textElement.setAttribute("title" ,confidence);
                this.$refs.textElement.setAttribute("data-original-title", confidence);
            } else {
                // remove confidence from title attributes
                this.$refs.textElement.setAttribute("title", "");
                this.$refs.textElement.setAttribute("data-original-title", "");
            }
        },
        edit() {
            if (this.activeTool !== "pan") {
                this.$store.dispatch("lines/toggleLineEdition", this.line);
            }
        },
        reset() {
            this.computeLineHeight();
            this.computeTextLength();
            this.computeConfidence();
        },
    }
});
</script>

<style scoped>
</style>
