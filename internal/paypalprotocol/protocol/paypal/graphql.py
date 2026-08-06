"""GraphQL query and mutation definitions for PayPal checkout flow."""

# Current checkoutweb/weasley CheckoutSessionDataQuery.  The old
# isChangePaymentMethodFlow flag no longer exists on CheckoutSessionFlags and
# causes GRAPHQL_VALIDATION_FAILED.
CHECKOUT_SESSION_DATA_QUERY = """
query CheckoutSessionDataQuery($token: String!) {
  checkoutSession(token: $token) {
    allowedCardIssuers
    cart {
      soldoutUrl
      showSoldoutPage
      ...CartAmounts
      ...CartItems
      ...CartOvercapture
      ...SubscriptionDetails
      billingAddress {
        city
        country
        line1
        line2
        postalCode
        state
        formattedFullAddress
        __typename
      }
      cancelUrl {
        href
        __typename
      }
      description
      email {
        stringValue
        __typename
      }
      intent
      noteToBuyer
      payer {
        name {
          familyName
          givenName
          __typename
        }
        __typename
      }
      formattedPhoneNumber(shouldValidate: true, useInternationalFormat: true)
      phoneNumber(shouldValidate: true, stripDialingCode: true)
      shippingAddress {
        city
        country
        firstName
        isStoreAddress
        lastName
        line1
        line2
        postalCode
        state
        formattedFullAddress
        __typename
      }
      ...ShippingMethods
      __typename
    }
    checkoutSessionType
    merchant {
      country
      merchantId
      name
      __typename
    }
    __typename
  }
}

fragment CartOvercapture on Cart {
  totalAllowedOverCaptureAmount {
    currencyFormatSymbolISOCurrency
    __typename
  }
  __typename
}

fragment CartAmounts on Cart {
  amounts {
    discount {
      currencyCode
      currencyFormatSymbolISOCurrency
      currencyValue
      currencyFormat
      __typename
    }
    handlingFee {
      currencyCode
      currencyFormatSymbolISOCurrency
      currencyValue
      currencyFormat
      __typename
    }
    insurance {
      currencyCode
      currencyFormatSymbolISOCurrency
      currencyValue
      currencyFormat
      __typename
    }
    shipping {
      currencyCode
      currencyFormatSymbolISOCurrency
      currencyValue
      currencyFormat
      __typename
    }
    shippingAndHandling {
      currencyCode
      currencyFormatSymbolISOCurrency
      currencyValue
      currencyFormat
      __typename
    }
    shippingDiscount {
      currencyCode
      currencyFormatSymbolISOCurrency
      currencyValue
      currencyFormat
      __typename
    }
    subtotal {
      currencyCode
      currencyFormatSymbolISOCurrency
      currencyValue
      currencyFormat
      __typename
    }
    tax {
      currencyCode
      currencyFormatSymbolISOCurrency
      currencyValue
      currencyFormat
      __typename
    }
    total {
      currencyCode
      currencyFormat
      currencyFormatSymbolISOCurrency
      currencyValue
      currencySymbol
      __typename
    }
    __typename
  }
  __typename
}

fragment CartItems on Cart {
  items {
    description
    details
    name
    quantity
    total {
      currencyFormatSymbolISOCurrency
      currencyValue
      __typename
    }
    unitPrice {
      currencyFormatSymbolISOCurrency
      currencyValue
      currencyFormat
      __typename
    }
    __typename
  }
  __typename
}

fragment SubscriptionDetails on Cart {
  subscriptionDetails {
    __typename
    initialPaymentInfo {
      __typename
      setupFee {
        __typename
        currencyCode
        currencyFormat
        currencyFormatSymbolISOCurrency
        currencyValue
      }
      firstPayment {
        __typename
        currencyCode
        currencyFormat
        currencyFormatSymbolISOCurrency
        currencyValue
      }
      shippingAmount {
        __typename
        currencyCode
        currencyFormat
        currencyFormatSymbolISOCurrency
        currencyValue
      }
      taxPercentage
      totalAmount {
        __typename
        currencyCode
        currencyFormat
        currencyFormatSymbolISOCurrency
        currencyValue
      }
    }
    plan {
      __typename
      id
      name
      billingCycles {
        __typename
        fixedPrice {
          __typename
          currencyCode
          currencyFormat
          currencyFormatSymbolISOCurrency
          currencyValue
        }
        frequency {
          __typename
          intervalUnit
          intervalCount
        }
        totalCycles
      }
      startTime
    }
  }
  __typename
}

fragment ShippingMethods on Cart {
  shippingMethods {
    id
    type
    label
    selected
    amount {
      currencyCode
      currencyFormatSymbolISOCurrency
      currencyValue
      __typename
    }
    __typename
  }
  __typename
}
"""

# Current checkoutweb/weasley GriffinMetadataQuery.  The old root field
# `griffin(token: ...)` was removed; locale metadata now lives under
# Query.localeMetadata.
GRIFFIN_METADATA_QUERY = """
query GriffinMetadataQuery($countryCode: CountryCodes!, $languageCode: CheckoutContentLanguageCode!, $shippingCountryCode: CountryCodes!) {
  localeMetadata {
    address(countryCode: $countryCode, languageCode: $languageCode) {
      ...AddressMetadata
      __typename
    }
    shippingAddress: address(
      countryCode: $shippingCountryCode
      languageCode: $languageCode
    ) {
      ...AddressMetadata
      __typename
    }
    currencyCode(countryCode: $countryCode)
    date(countryCode: $countryCode, languageCode: $languageCode) {
      displayFormat
      datePattern
      __typename
    }
    phone(countryCode: $countryCode) {
      masks {
        mobile
        __typename
      }
      patterns {
        default
        __typename
      }
      __typename
    }
    territories(countryCode: $countryCode, languageCode: $languageCode) {
      code
      internationalDialingCode
      name
      region
      suggestedDefaultLanguage
      __typename
    }
    __typename
  }
}

fragment AddressMetadata on LocaleAddressMetadata {
  layout {
    maxLength
    minLength
    isRequired
    name
    regex
    __typename
  }
  strings {
    cityLabel
    line1Label
    line2Label
    optionalLabel
    postcodeLabel
    stateLabel
    stateList {
      displayText
      value
      __typename
    }
    __typename
  }
  __typename
}
"""

# Current checkoutweb no longer uses a supportedFundingSources root query in this
# path.  Keep a harmless query shape for callers that still want a warm-up call.
SUPPORTED_FUNDING_SOURCES_QUERY = """
query SupportedFundingSourcesQuery($token: String!, $userCountry: CountryCodes) {
  checkoutSession(token: $token) {
    supportedFundingSources(userCountry: $userCountry) {
      issuers {
        name
        usage
        issuerLogoUrl {
          href
          __typename
        }
        rank
        __typename
      }
      __typename
    }
    __typename
  }
}
"""

INSTALLMENT_OPTIONS_QUERY = """
query InstallmentOptionsQuery($buyerCountry: CountryCodes!, $cardNumber: String!, $cardType: CardIssuerType, $token: String!) {
  getInstallmentsForOnboardingFlows(
    buyerCountry: $buyerCountry
    cardNumber: $cardNumber
    cardType: $cardType
    token: $token
  ) {
    discount {
      amount {
        currencyCode
        currencyFormat
        currencyFormatSymbolISOCurrency
        currencySymbol
        currencyValue
        __typename
      }
      percentage
      __typename
    }
    monthlyPayment {
      currencyCode
      currencyFormat
      currencyFormatSymbolISOCurrency
      currencySymbol
      currencyValue
      __typename
    }
    term
    totalCost {
      currencyCode
      currencyFormat
      currencyFormatSymbolISOCurrency
      currencySymbol
      currencyValue
      __typename
    }
    totalConsumerFee {
      amount {
        currencyCode
        currencyFormat
        currencyFormatSymbolISOCurrency
        currencySymbol
        currencyValue
        __typename
      }
      percentage
      __typename
    }
    feeReferenceId
    __typename
  }
}
"""

ADDRESS_AUTOCOMPLETE_FROM_POSTAL_CODE_QUERY = """
query AddressAutocompleteFromPostalCodeQuery($postalCode: String!, $token: String!, $country: CountryCodes) {
  addressNormalization(
    postalCode: $postalCode
    token: $token
    processMode: FASTCOMPLETION
    scope: STREET_LEVEL
    country: $country
  ) {
    line1
    line2
    city
    state
    postalCode
    __typename
  }
}
"""

# Browser Weasley calls this early on signup.  The otpLoginContext payload is
# not required by this lightweight flow, but the call warms the session and
# keeps the GraphQL sequence closer to the captured browser trace.
DEFERRED_FEATURE_QUERY = """
query DeferredFeature($token: String!, $integrationType: String!) {
  otpLoginContext(token: $token, integrationType: $integrationType) {
    __typename
    context
  }
}
"""

INITIAL_DATA_QUERY = """
query InitialDataQuery($channel: String!, $countryCode: CountryCodes!, $countryCodeAsString: String!, $isBaslAsString: String!, $languageCode: CheckoutContentLanguageCode!, $token: String!, $fundingSource: ButtonFundingSourceType) {
  checkoutSession(token: $token) {
    __typename
    allowedCardIssuers
    cardProductClasses
    cart {
      __typename
      amounts {
        __typename
        discount {
          __typename
          currencyCode
          currencyValue
        }
        handlingFee {
          __typename
          currencyCode
          currencyValue
        }
        hideAmountDetails
        insurance {
          __typename
          currencyCode
          currencyValue
        }
        insuranceAndHandling {
          __typename
          currencyCode
          currencyValue
        }
        shipping {
          __typename
          currencyCode
          currencyValue
        }
        shippingAndShippingDiscount {
          __typename
          currencyCode
          currencyValue
        }
        shippingDiscount {
          __typename
          currencyCode
          currencyValue
        }
        subtotal {
          __typename
          currencyCode
          currencyValue
        }
        tax {
          __typename
          currencyCode
          currencyValue
        }
        total {
          __typename
          currencyCode
          currencyValue
          currencyFormatSymbolISOCurrency
          currencyFormat
          currencySymbol
        }
      }
      billingType
      billingAddress {
        __typename
        city
        country
        line1
        line2
        postalCode
        state
        formattedFullAddress
      }
      callbackIntegrationType
      callbackVersion
      cancelUrl {
        __typename
        href
      }
      consentCode
      email {
        __typename
        stringValue
      }
      intent
      items {
        __typename
        name
        quantity
      }
      payer {
        evaluatedUserData {
          __typename
          emailHasExistingAccount
        }
        __typename
        name {
          __typename
          familyName
          givenName
        }
      }
      formattedPhoneNumber(shouldValidate: true, useInternationalFormat: true)
      phoneNumber(shouldValidate: true, stripDialingCode: true)
      totalAllowedOverCaptureAmount {
        currencyFormatSymbolISOCurrency
        __typename
      }
      shippingAddress {
        __typename
        city
        country
        firstName
        isStoreAddress
        lastName
        line1
        line2
        postalCode
        state
        formattedFullAddress
      }
      shippingMethods {
        __typename
        id
        type
        label
        selected
        amount {
          __typename
          currencyCode
          currencyFormatSymbolISOCurrency
          currencyValue
        }
      }
      soldoutUrl
      showSoldoutPage
      subscriptionDetails {
        __typename
        initialPaymentInfo {
          __typename
          setupFee {
            __typename
            currencyCode
            currencyFormat
            currencyFormatSymbolISOCurrency
            currencyValue
          }
          firstPayment {
            __typename
            currencyCode
            currencyFormat
            currencyFormatSymbolISOCurrency
            currencyValue
          }
          shippingAmount {
            __typename
            currencyCode
            currencyFormat
            currencyFormatSymbolISOCurrency
            currencyValue
          }
          taxPercentage
          totalAmount {
            __typename
            currencyCode
            currencyFormat
            currencyFormatSymbolISOCurrency
            currencyValue
          }
        }
        plan {
          __typename
          id
          name
          billingCycles {
            __typename
            fixedPrice {
              __typename
              currencyCode
              currencyFormat
              currencyFormatSymbolISOCurrency
              currencyValue
            }
            frequency {
              __typename
              intervalUnit
              intervalCount
            }
            totalCycles
          }
          startTime
        }
      }
    }
    checkoutSessionType
    creditMeta {
      __typename
      upstreamCreditPresentment
    }
    flags {
      __typename
      blockNonDomesticPayer
      blockNonDomesticShipping
      blockNonDomesticCards
      hasPayLaterIntent
      hideShipping
      isBillingAgreement
      isChangeShippingAddressAllowed
      isDebitRequired
      isDonation
      isGuestEligible(fundingSource: $fundingSource)
      isInvoiceFlow
      isOptionallyShareAddress
      isShippingAddressRequired
      isSntSupported
      isSignupIncentiveEligible
      isSignupIncentiveEligibleStretchRewards
      isWPS
      isWpsAutobilling
      isWpsInstallmentPlan
      isWpsSubscription
      payLaterOnly
    }
    merchant {
      __typename
      cobrands
      country
      logo {
        __typename
        href
      }
      merchantId
      name
      billPayIntegratorName
      preferences {
        __typename
        autoReturnToMerchant
        enablePaymentDataTransfer
      }
    }
    nonMemberCreditOffers(countryCode: $countryCode, languageCode: $languageCode) {
      __typename
      allOffers {
        __typename
        creditProductIdentifier
        isEligible
      }
    }
    userAction
  }
  elmoExperimentWithRealTimeFilters(
    app: "checkoutuinodeweb"
    filters: [{key: "Country", value: $countryCodeAsString}, {key: "Channel", value: $channel}, {key: "IsBasl", value: $isBaslAsString}, {key: "clientApp", value: "weasley"}]
    realTimeFilters: [{key: "IsGuestEligible", value: IS_GUEST_ELIGIBLE}, {key: "IsWPS", value: IS_WPS}, {key: "checkoutSessionType", value: CHECKOUT_SESSION_TYPE}, {key: "billingType", value: BILLING_TYPE}, {key: "MerchantCountry", value: MERCHANT_COUNTRY}, {key: "MerchantInCustomerListName", value: MERCHANT_IN_CUSTOMER_LIST_NAME, args: {segmentName: OPTIONAL_GUEST_MERCHANTS_SEGMENT}}, {key: "MerchantInRMGListName", value: MERCHANT_IN_CUSTOMER_LIST_NAME, args: {segmentName: RMG_MERCHANTS_SEGMENT}}, {key: "MerchantInSafeMerchantListName", value: MERCHANT_IN_CUSTOMER_LIST_NAME, args: {segmentName: SIGNUP_INCENTIVES_SAFE_MERCHANTS_SEGMENT}}]
    res: "weasley:featureFlags,XO_LANDING_AND_WEALEY_MERCHANT_BASED_FLOW_TYPE,xobuyer:onboarding::weblls, weasley:featureFlags::vip, pp:rebrand:2025, weasley:featureFlags::guestBankBlock, au_cip_frontbook"
    token: $token
  ) {
    __typename
    treatments {
      __typename
      experimentId
      experimentName
      factors {
        __typename
        key
        value
      }
      treatmentId
      treatmentName
    }
  }
  localeMetadata {
    __typename
    businessRegions(countryCode: $countryCode)
    currencyCode(countryCode: $countryCode)
    currencySymbol(countryCode: $countryCode, languageCode: $languageCode)
    date(countryCode: $countryCode, languageCode: $languageCode) {
      __typename
      displayFormat
      datePattern
    }
    phone(countryCode: $countryCode) {
      __typename
      masks {
        __typename
        mobile
      }
      patterns {
        __typename
        default
      }
    }
    suggestedDefaultLanguage(countryCode: $countryCode)
    supportedLanguages(countryCode: $countryCode)
  }
  unifiedLoginContext(token: $token) {
    __typename
    experienceMetadata {
      __typename
      onboardingData {
        __typename
        email {
          __typename
          stringValue
        }
        applePayButtonSeen
        loginMethod
        hasExistingAccount
        userIntent
      }
    }
  }
  userAgreement(flow: ONBOARDING) {
    minorVersion
    majorVersion
    activeUrl
    __typename
  }
}
"""

COOKIE_BANNER_QUERY = """
query CookieBannerQuery {
  cookieBanner {
    js
    css
    html
    __typename
  }
}
"""

# Step 1: Send SMS OTP
INITIATE_2FA_PHONE_MUTATION = """
mutation InitiateRiskBasedTwoFactorPhoneConfirmationMutation($phoneNumber: String!, $locale: LocaleInput!, $phoneCountry: CountryCodes!, $token: String!) {
  initiateRiskBasedTwoFactorPhoneConfirmation(
    locale: $locale
    phoneCountry: $phoneCountry
    phoneNumber: $phoneNumber
    token: $token
  ) {
    authId
    challengeId
    state
    __typename
  }
}
"""

# Step 2: Verify SMS OTP pin
CONFIRM_2FA_PHONE_MUTATION = """
mutation ConfirmRiskBasedTwoFactorPhoneConfirmationMutation($pin: String!, $authId: String!, $challengeId: String!, $token: String!) {
  confirmRiskBasedTwoFactorPhoneConfirmation(
    pin: $pin
    authId: $authId
    challengeId: $challengeId
    token: $token
  ) {
    authId
    challengeId
    state
    __typename
  }
}
"""

# Step 3: Create account + add card + onboard
SIGNUP_NEW_MEMBER_MUTATION = """
mutation SignUpNewMemberMutation($bank: BankAccountInput, $billingAddress: AddressInput, $card: CardInput, $contentIdentifier: String, $country: CountryCodes, $countrySpecificFirstName: String, $countrySpecificLastName: String, $crsData: CommonReportingStandardsInput, $currencyConversionType: CheckoutCurrencyConversionType, $dateOfBirth: DateOfBirth, $email: String!, $firstName: String!, $gender: Gender, $identityDocument: IdentityDocumentInput, $lastName: String!, $middleName: String, $marketingOptOut: Boolean, $nationality: CountryCodes, $occupation: Occupation, $password: String, $phone: PhoneInput!, $placeOfBirth: CountryCodes, $secondaryIdentityDocument: IdentityDocumentInput, $selectedInstallmentOption: InstallmentsInput, $shareAddressWithDonatee: Boolean, $shippingAddress: AddressInput, $supportedThreeDsExperiences: [ThreeDSPaymentExperience], $token: String!, $residentialAddress: AddressInput, $isSignupIncentiveOptIn: Boolean, $isSignupIncentiveOptInStretch: Boolean, $legalAgreements: LegalAgreementsInput, $collectedConsents: [CollectedConsent]) {
  onboardAccount: signUpNewMember(
    bank: $bank
    billingAddress: $billingAddress
    card: $card
    contentIdentifier: $contentIdentifier
    countrySpecificFirstName: $countrySpecificFirstName
    countrySpecificLastName: $countrySpecificLastName
    country: $country
    crsData: $crsData
    currencyConversionType: $currencyConversionType
    dateOfBirth: $dateOfBirth
    email: $email
    firstName: $firstName
    gender: $gender
    identityDocument: $identityDocument
    lastName: $lastName
    middleName: $middleName
    marketingOptOut: $marketingOptOut
    nationality: $nationality
    occupation: $occupation
    password: $password
    phone: $phone
    placeOfBirth: $placeOfBirth
    secondaryIdentityDocument: $secondaryIdentityDocument
    selectedInstallmentOption: $selectedInstallmentOption
    shareAddressWithDonatee: $shareAddressWithDonatee
    shippingAddress: $shippingAddress
    token: $token
    residentialAddress: $residentialAddress
    isSignupIncentiveOptIn: $isSignupIncentiveOptIn
    isSignupIncentiveOptInStretch: $isSignupIncentiveOptInStretch
    legalAgreements: $legalAgreements
    collectedConsents: $collectedConsents
  ) {
    ...buyer
    flags {
      is3DSecureRequired
      __typename
    }
    ...fundingOptions
    paymentContingencies {
      ...threeDomainSecure
      ...threeDSContingencyData
      __typename
    }
    __typename
  }
}

fragment buyer on CheckoutSession {
  buyer {
    auth {
      accessToken
      __typename
    }
    userId
    __typename
  }
  __typename
}

fragment fundingOptions on CheckoutSession {
  fundingOptions {
    allPlans {
      fundingSources {
        fundingInstrument {
          id
          __typename
        }
        amount {
          currencyCode
          currencyValue
          __typename
        }
        __typename
      }
      fundingContingencies {
        ... on OpenBankingContingency {
          encryptedId
          contingencyReasons
          contingencyType
          __typename
        }
        __typename
      }
      __typename
    }
    fundingInstrument {
      id
      lastDigits
      name
      nameDescription
      type
      __typename
    }
    __typename
  }
  __typename
}

fragment threeDomainSecure on PaymentContingencies {
  threeDomainSecure(experiences: $supportedThreeDsExperiences) {
    status
    redirectUrl {
      href
      __typename
    }
    method
    parameter
    experience
    requestParams {
      key
      value
      __typename
    }
    __typename
  }
  __typename
}

fragment threeDSContingencyData on PaymentContingencies {
  threeDSContingencyData {
    name
    causeName
    resolution {
      type
      resolutionName
      paymentCard {
        billingAddress {
          line1
          line2
          city
          state
          country
          postalCode
          __typename
        }
        expireYear
        expireMonth
        currencyCode
        cardProductClass
        id
        encryptedNumber
        type
        number
        bankIdentificationNumber
        __typename
      }
      contingencyContext {
        deviceDataCollectionUrl {
          href
          __typename
        }
        jwtSpecification {
          jwtDuration
          jwtIssuer
          jwtOrgUnitId
          type
          __typename
        }
        authenticationProvider
        cardBrandProcessed
        reason
        referenceId
        source
        __typename
      }
      __typename
    }
    __typename
  }
  __typename
}
"""

# Step 4: Final billing agreement authorization
AUTHORIZE_BILLING_MUTATION = """
mutation authorize($billingAgreementId: String!, $addressId: String, $fundingPreference: billingFundingPreferenceInput, $legalAgreements: billingLegalAgreementsInput) {
  billing {
    authorize(
      billingAgreementId: $billingAgreementId
      addressId: $addressId
      fundingPreference: $fundingPreference
      legalAgreements: $legalAgreements
    ) {
      billingAgreementToken
      paymentAction
      returnURL {
        href
        __typename
      }
      buyer {
        userId
        __typename
      }
      __typename
    }
    __typename
  }
}
"""
