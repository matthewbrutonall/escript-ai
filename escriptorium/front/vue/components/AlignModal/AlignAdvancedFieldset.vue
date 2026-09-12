<template>
    <fieldset class="escr-align-advanced">
        <legend><h3>{{ $t("align.advanced") }}</h3></legend>
        <label class="escr-text-field">
            <div>
                <span>{{ $t("align.threshold") }}</span>
                <VDropdown
                    theme="escr-tooltip"
                    @apply-show="showTooltip({ form: 'align', tooltip: 'threshold' })"
                    @apply-hide="hideTooltip({ form: 'align', tooltip: 'threshold' })"
                >
                    <EscrButton
                        color="link-secondary"
                        size="small"
                        :disabled="disabled"
                        :on-click="() => {}"
                    >
                        <template #button-icon>
                            <InfoFilledIcon v-if="tooltipShown && tooltipShown.threshold" />
                            <InfoOutlineIcon v-else />
                        </template>
                    </EscrButton>
                    <template #popper>
                        <span>
                            {{ $t("align.thresholdHelp") }}
                        </span>
                    </template>
                </VDropdown>
            </div>
            <input
                type="number"
                step="0.1"
                min="0.0"
                max="1.0"
                :placeholder="$t('align.threshold')"
                :disabled="disabled"
                :value="threshold"
                @change="(e) => handleChange('threshold', e.target.value)"
            >
        </label>
        <label class="escr-text-field">
            <div>
                <span>{{ $t("align.ngram") }}</span>
                <VDropdown
                    theme="escr-tooltip"
                    @apply-show="showTooltip({ form: 'align', tooltip: 'ngram' })"
                    @apply-hide="hideTooltip({ form: 'align', tooltip: 'ngram' })"
                >
                    <EscrButton
                        color="link-secondary"
                        size="small"
                        :disabled="disabled"
                        :on-click="() => {}"
                    >
                        <template #button-icon>
                            <InfoFilledIcon v-if="tooltipShown && tooltipShown.ngram" />
                            <InfoOutlineIcon v-else />
                        </template>
                    </EscrButton>
                    <template #popper>
                        <span>
                            {{ $t("align.ngramHelp") }}
                        </span>
                    </template>
                </VDropdown>
            </div>
            <input
                type="number"
                min="2"
                max="25"
                :placeholder="$t('align.ngram')"
                :disabled="disabled"
                :value="ngram"
                @change="(e) => handleChange('ngram', e.target.value)"
            >
        </label>
        <label class="escr-text-field">
            <div>
                <span>{{ $t("align.beam") }}</span>
                <VDropdown
                    theme="escr-tooltip"
                    @apply-show="showTooltip({ form: 'align', tooltip: 'beamSize' })"
                    @apply-hide="hideTooltip({ form: 'align', tooltip: 'beamSize' })"
                >
                    <EscrButton
                        color="link-secondary"
                        size="small"
                        :disabled="disabled"
                        :on-click="() => {}"
                    >
                        <template #button-icon>
                            <InfoFilledIcon v-if="tooltipShown && tooltipShown.beamSize" />
                            <InfoOutlineIcon v-else />
                        </template>
                    </EscrButton>
                    <template #popper>
                        <span>
                            {{ $t("align.beamHelp") }}
                        </span>
                    </template>
                </VDropdown>
            </div>
            <input
                type="number"
                min="0"
                max="100"
                :placeholder="$t('align.beam')"
                :disabled="disabled || maxOffset !== ''"
                :value="beamSize"
                @change="(e) => handleChange('beamSize', e.target.value)"
            >
        </label>
        <label class="escr-text-field">
            <div>
                <span>{{ $t("align.maxOffset") }}</span>
                <VDropdown
                    theme="escr-tooltip"
                    @apply-show="showTooltip({ form: 'align', tooltip: 'maxOffset' })"
                    @apply-hide="hideTooltip({ form: 'align', tooltip: 'maxOffset' })"
                >
                    <EscrButton
                        color="link-secondary"
                        size="small"
                        :disabled="disabled"
                        :on-click="() => {}"
                    >
                        <template #button-icon>
                            <InfoFilledIcon v-if="tooltipShown && tooltipShown.maxOffset" />
                            <InfoOutlineIcon v-else />
                        </template>
                    </EscrButton>
                    <template #popper>
                        <span>
                            {{ $t("align.maxOffsetHelp") }}
                        </span>
                    </template>
                </VDropdown>
            </div>
            <input
                type="number"
                min="0"
                max="80"
                :placeholder="$t('align.maxOffset')"
                :disabled="disabled || beamSize !== ''"
                :value="maxOffset"
                @change="(e) => handleChange('maxOffset', e.target.value)"
            >
        </label>
        <label class="escr-text-field">
            <div>
                <span>{{ $t("align.gap") }}</span>
                <VDropdown
                    theme="escr-tooltip"
                    @apply-show="showTooltip({ form: 'align', tooltip: 'gap' })"
                    @apply-hide="hideTooltip({ form: 'align', tooltip: 'gap' })"
                >
                    <EscrButton
                        color="link-secondary"
                        size="small"
                        :disabled="disabled"
                        :on-click="() => {}"
                    >
                        <template #button-icon>
                            <InfoFilledIcon v-if="tooltipShown && tooltipShown.gap" />
                            <InfoOutlineIcon v-else />
                        </template>
                    </EscrButton>
                    <template #popper>
                        <span>
                            {{ $t("align.gapHelp") }}
                        </span>
                    </template>
                </VDropdown>
            </div>
            <input
                type="number"
                min="1"
                max="1000000"
                :placeholder="$t('align.gap')"
                :disabled="disabled"
                :value="gap"
                @change="(e) => handleChange('gap', e.target.value)"
            >
        </label>
    </fieldset>
</template>
<script>
import { mapActions, mapState } from "vuex";
import EscrButton from "../Button/Button.vue";
import InfoFilledIcon from "../Icons/InfoFilledIcon/InfoFilledIcon.vue";
import InfoOutlineIcon from "../Icons/InfoOutlineIcon/InfoOutlineIcon.vue";
import { Dropdown as VDropdown } from "floating-vue";

export default {
    name: "EscrAlignAdvancedFieldset",
    components: { EscrButton, InfoFilledIcon, InfoOutlineIcon, VDropdown },
    props: {
        /**
         * Boolean indicating whether or not the form fields should be disabled.
         */
        disabled: {
            type: Boolean,
            required: true,
        },
    },
    computed: {
        ...mapState({
            threshold: (state) => state.forms.align.threshold,
            ngram: (state) => state.forms.align.ngram,
            beamSize: (state) => state.forms.align.beamSize,
            maxOffset: (state) => state.forms.align.maxOffset,
            gap: (state) => state.forms.align.gap,
            tooltipShown: (state) => state.forms.align.tooltipShown,
        }),
    },
    mounted() {
        // scroll into view on mount
        this.$el.scrollIntoView({ behavior: "smooth" });
    },
    methods: {
        ...mapActions("forms", [
            "handleGenericInput",
            "hideTooltip",
            "showTooltip",
        ]),
        handleChange(field, value) {
            this.handleGenericInput({ form: "align", field, value });
        },
    },
}
</script>
