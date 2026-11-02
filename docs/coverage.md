# Walmart coverage

## Coverage and prerequisites

This repository covers **Walmart US** at `www.walmart.com` only.
It does not claim Walmart Canada, Mexico or other markets. Marketplace sellers
may appear in the US listings; verify the seller and selected variant before
comparing products.

You need a datawebot account with approved email sign-in, API access and enough
credits. Source availability and maintenance can change; live discovery is
authoritative. Read the [terms](https://www.datawebot.com/terms) and use data only
where you have the necessary rights.

| Operation | Endpoint ID shown in the public site guide |
| --- | --- |
| Search | `walmart.search` |
| Product details | `walmart.product_details` |

These are reference IDs, not a substitute for discovery. Confirm them before
running work, and use the returned contracts rather than guessing IDs.

Search URLs use `/search?q=URL_ENCODED_KEYWORD`. The published site guide
allows an explicit search page 1 or 2, not automatic pagination or search filters.
Product URLs use `/ip/slug/item-id`; choose a real URL from search results.
Do not add `max_pages` to the legacy CLI parameters unless discovery allows it.
No whole-catalogue extraction or local-store inventory is promised.
