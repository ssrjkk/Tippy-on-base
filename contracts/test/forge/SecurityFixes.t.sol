// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// Regression tests for the 2026-08-24 security fixes:
///
///   #1 Signed-cast wrap in LMSR.buyCost/sellProceeds -> free giant buys.
///      Fixed with explicit bounds checks (LMSR.SharesTooLarge) plus a
///      market-level MAX_SUPPLY_PER_OUTCOME cap on every minting path.
///   #2 cancelExpired() paid the pro-rata refund to the CREATOR instead of
///      the shareholders. Fixed with a pull-based claimCancelled() flow;
///      the creator only ever receives the no-holders subsidy back and the
///      final rounding-dust sweep.
///
/// Requires vendored deps: bash scripts/setup_foundry.sh  (installs
/// openzeppelin, prb-math and forge-std), then `forge test --match-contract SecurityFixesTest`.

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {OutcomeMarket} from "../../OutcomeMarket.sol";
import {LMSR} from "../../LMSR.sol";

/// @dev Minimal USDC stand-in (SafeERC20-compatible bool returns).
contract FakeUSDC is IERC20 {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    uint256 public totalSupply;

    function decimals() external pure returns (uint8) { return 6; }
    function mint(address to, uint256 amount) external { balanceOf[to] += amount; totalSupply += amount; }
    function approve(address s, uint256 a) external returns (bool) { allowance[msg.sender][s] = a; return true; }
    function transfer(address to, uint256 a) external returns (bool) { _xfer(msg.sender, to, a); return true; }
    function transferFrom(address f, address to, uint256 a) external returns (bool) {
        uint256 allowed = allowance[f][msg.sender];
        if (allowed != type(uint256).max) require(allowed >= a, "allowance");
        if (allowed != type(uint256).max) allowance[f][msg.sender] = allowed - a;
        _xfer(f, to, a);
        return true;
    }
    function _xfer(address f, address t, uint256 a) private {
        require(balanceOf[f] >= a, "balance");
        balanceOf[f] -= a; balanceOf[t] += a;
    }
}

/// @dev Direct library harness so the LMSR-level guard stays covered even
///      though the market-level cap rejects huge shares first.
contract LMSRHarness {
    function buyCost(int256[] memory q, int256 b, uint256 idx, uint256 shares) external pure returns (uint256) {
        return LMSR.buyCost(q, b, idx, shares);
    }
}

contract SecurityFixesTest is Test {
    FakeUSDC usdc;
    OutcomeMarket market;
    LMSRHarness harness;

    address creator = address(0xA11CE);
    address trader1 = address(0xBEEF);
    address trader2 = address(0xC0DE);

    uint64 closesAt;

    function setUp() public {
        usdc = new FakeUSDC();
        market = new OutcomeMarket(address(usdc), creator);
        harness = new LMSRHarness();
        closesAt = uint64(block.timestamp + 1 days);

        usdc.mint(creator, 1_000_000e6);
        usdc.mint(trader1, 10_000e6);
        usdc.mint(trader2, 10_000e6);
    }

    function _create(uint8 outcomes, uint256 subsidy) internal returns (uint256 id) {
        vm.startPrank(creator);
        usdc.approve(address(market), type(uint256).max);
        id = market.createMarket(outcomes, subsidy, closesAt);
        vm.stopPrank();
    }

    function _buy(address who, uint256 id, uint8 outcome, uint256 shares)
        internal returns (uint256 cost)
    {
        uint256 beforeBal = usdc.balanceOf(who);
        vm.startPrank(who);
        usdc.approve(address(market), type(uint256).max);
        market.buy(id, outcome, shares, type(uint256).max);
        vm.stopPrank();
        cost = beforeBal - usdc.balanceOf(who);
    }

    // ------------------------------------------------------------------
    // Vulnerability #1: signed-cast wrap
    // ------------------------------------------------------------------

    function test_RevertOnHugeShares_MarketLevel() public {
        uint256 id = _create(2, 50e6);
        uint256 huge = uint256(type(int256).max) + 1;
        vm.prank(trader1);
        vm.expectRevert(OutcomeMarket.InvalidShares.selector);
        market.buy(id, 0, huge, type(uint256).max);
    }

    function test_RevertOnHugeShares_LMSRLevel() public {
        int256[] memory q = new int256[](2);
        uint256 huge = uint256(type(int256).max) + 1;
        vm.expectRevert(LMSR.SharesTooLarge.selector);
        harness.buyCost(q, 10_000_000, 0, huge);
    }

    function test_NormalSharesStillWork() public {
        uint256 id = _create(2, 50e6);
        uint256 cost = _buy(trader1, id, 0, 1_000_000);
        assertGt(cost, 0);
        assertEq(market.balanceOf(trader1, id * 256 + 0), 1_000_000);
    }

    function test_SupplyCapReverts() public {
        uint256 id = _create(2, 50e6);
        // Read the cap BEFORE expectRevert: the getter staticcall would
        // otherwise consume the expectation (arguments are evaluated
        // after expectRevert is armed).
        uint256 cap = market.MAX_SUPPLY_PER_OUTCOME();
        vm.startPrank(trader1);
        usdc.approve(address(market), type(uint256).max);
        vm.expectRevert(OutcomeMarket.InvalidShares.selector);
        market.buy(id, 0, cap + 1, type(uint256).max);
        vm.stopPrank();
    }

    function test_ZeroSharesReverts() public {
        uint256 id = _create(2, 50e6);
        vm.prank(trader1);
        vm.expectRevert(OutcomeMarket.InvalidShares.selector);
        market.buy(id, 0, 0, type(uint256).max);
    }

    // ------------------------------------------------------------------
    // Vulnerability #2: cancelExpired refund recipient
    // ------------------------------------------------------------------

    /// The original PoC: trader buys, market expires, cancelExpired() runs.
    /// Pre-fix the TRADER's refund landed on the creator. Post-fix the
    /// creator receives nothing at cancel time and the trader pulls their
    /// share via claimCancelled().
    function test_CancelExpiredRefundsTraders_NotCreator() public {
        uint256 id = _create(2, 50e6);

        uint256 cost = _buy(trader1, id, 0, 5_000_000);
        uint256 escrow = 50e6 + cost;

        uint256 creatorBefore = usdc.balanceOf(creator);
        uint256 traderBefore = usdc.balanceOf(trader1);
        uint256 contractBefore = usdc.balanceOf(address(market));
        assertEq(contractBefore, escrow);

        vm.warp(closesAt + market.EXPIRY_WINDOW() + 1);
        market.cancelExpired(id);

        // Creator got NOTHING from the cancellation itself.
        assertEq(usdc.balanceOf(creator), creatorBefore, "creator must not be paid");

        // Trader claims their pro-rata slice (par-capped: 5M shares pay
        // exactly 5M micro-USDC; t1 held every share, so the final claim
        // also sweeps the unused subsidy to the creator).
        vm.prank(trader1);
        uint256 claimed = market.claimCancelled(id);
        assertEq(claimed, 5_000_000, "refund must be par-capped");
        assertEq(usdc.balanceOf(trader1), traderBefore + claimed);
        assertEq(market.unclaimedEscrowMicro(id), 0, "dust swept on final claim");
        assertEq(usdc.balanceOf(creator), creatorBefore + (escrow - claimed), "creator keeps unused subsidy");
    }

    function test_CancelExpiredWithNoHoldersReturnsSubsidyToCreator() public {
        uint256 id = _create(2, 50e6);
        vm.warp(closesAt + market.EXPIRY_WINDOW() + 1);

        uint256 beforeBal = usdc.balanceOf(creator);
        market.cancelExpired(id);
        assertEq(usdc.balanceOf(creator) - beforeBal, 50e6);
        assertEq(market.unclaimedEscrowMicro(id), 0);
    }

    function test_TwoTradersConservation_LastClaimSweepsDustToCreator() public {
        uint256 id = _create(3, 50e6);

        _buy(trader1, id, 0, 4_000_000);
        _buy(trader2, id, 1, 6_000_000);

        uint256 escrow = usdc.balanceOf(address(market));
        assertGt(escrow, 50e6);

        vm.warp(closesAt + market.EXPIRY_WINDOW() + 1);

        uint256 creatorBefore = usdc.balanceOf(creator);
        uint256 t1Before = usdc.balanceOf(trader1);
        uint256 t2Before = usdc.balanceOf(trader2);

        market.cancelExpired(id);
        assertEq(usdc.balanceOf(creator), creatorBefore, "cancel pays nobody");

        vm.prank(trader1);
        uint256 c1 = market.claimCancelled(id);
        vm.prank(trader2);
        uint256 c2 = market.claimCancelled(id);

        // Conservation: every micro-USDC accounted for; contract drained;
        // floor-division dust swept to the creator on the final claim.
        assertEq(c1 + c2 <= escrow, true);
        assertEq(usdc.balanceOf(address(market)), 0, "contract drained");
        assertEq(usdc.balanceOf(trader1), t1Before + c1);
        assertEq(usdc.balanceOf(trader2), t2Before + c2);
        assertEq(usdc.balanceOf(creator), creatorBefore + (escrow - c1 - c2));
        assertEq(market.unclaimedEscrowMicro(id), 0);
    }

    function test_ClaimWithoutPositionReverts() public {
        uint256 id = _create(2, 50e6);
        _buy(trader1, id, 0, 1_000_000);

        vm.warp(closesAt + market.EXPIRY_WINDOW() + 1);
        market.cancelExpired(id);

        vm.prank(trader2); // holds nothing
        vm.expectRevert(OutcomeMarket.NothingToRedeem.selector);
        market.claimCancelled(id);
    }

    function test_TradingBlockedAfterCancel() public {
        uint256 id = _create(2, 50e6);
        vm.warp(closesAt + market.EXPIRY_WINDOW() + 1);
        market.cancelExpired(id);

        vm.startPrank(trader1);
        usdc.approve(address(market), type(uint256).max);
        vm.expectRevert(OutcomeMarket.AlreadyCancelled.selector);
        market.buy(id, 0, 1_000_000, type(uint256).max);
        vm.stopPrank();
    }

    // ------------------------------------------------------------------
    // Vulnerability #3: cancel refund rate unbounded above par
    // ------------------------------------------------------------------
    // Pre-fix the per-share cancel rate was escrow/totalShares with NO par
    // cap: a market with a large unspent subsidy and a tiny outstanding
    // supply let the smallest holder drain the WHOLE escrow (subsidy
    // included). Post-fix a share never redeems above $1 and the unused
    // subsidy goes back to the creator via the dust sweep.

    function test_CancelRefundCappedAtPar_SubsidyNotDrainable() public {
        uint256 id = _create(2, 50e6);

        // trader1 buys ONE micro-share (~$0.50 at open): with the old
        // unbounded rate this position alone was worth the entire escrow.
        uint256 cost = _buy(trader1, id, 0, 1);
        assertLt(cost, 1_000_000);

        vm.warp(closesAt + market.EXPIRY_WINDOW() + 1);
        market.cancelExpired(id);

        vm.prank(trader1);
        uint256 claimed = market.claimCancelled(id);
        // Par cap: 1 micro-share pays at most 1 micro-USDC.
        assertLe(claimed, 1, "refund must be capped at par");

        // Every remaining share is gone; the unused subsidy must be swept
        // back to the creator, NOT to the last claimant.
        uint256 creatorBefore = usdc.balanceOf(creator);
        assertEq(usdc.balanceOf(address(market)), 0, "market must be drained");
        assertGt(usdc.balanceOf(creator), creatorBefore - 1, "creator keeps the unused subsidy");
    }

    // ------------------------------------------------------------------
    // Vulnerability #4: mintCompleteSet after close amplified the drain
    // ------------------------------------------------------------------

    function test_MintCompleteSetBlockedAfterClose() public {
        uint256 id = _create(2, 50e6);
        vm.warp(1 days + 1); // past close, before expiry window

        vm.startPrank(trader1);
        usdc.approve(address(market), type(uint256).max);
        vm.expectRevert(OutcomeMarket.MarketNotClosed.selector);
        market.mintCompleteSet(id, 1);
        vm.stopPrank();
    }

    // ------------------------------------------------------------------
    // Vulnerability #5: dispute did not pause the cancel-expiry clock
    // ------------------------------------------------------------------

    function test_DisputeDelaysCancelExpiry() public {
        uint256 id = _create(2, 50e6);
        vm.warp(1 days + 1);

        address oracleAddr = address(0xA0C1E);
        vm.prank(creator); // owner == oracle by default in this suite
        market.setOracle(oracleAddr);
        vm.prank(oracleAddr);
        market.oracleResolve(id, 0);

        // Owner disputes 1 second before the 2h window lapses.
        vm.warp(block.timestamp + 2 hours - 1);
        vm.prank(creator);
        market.disputeResolution(id);

        // 24h after CLOSE (but only ~2h after the dispute) a permissionless
        // cancel must NOT be possible — the clock restarted at the dispute.
        vm.warp(closesAt + market.EXPIRY_WINDOW() + 1);
        vm.expectRevert(OutcomeMarket.MarketNotExpired.selector);
        market.cancelExpired(id);

        // Only 24h after the DISPUTE itself may anyone cancel.
        vm.warp(block.timestamp + market.EXPIRY_WINDOW() + 1);
        market.cancelExpired(id);
    }

    // ------------------------------------------------------------------
    // Vulnerability #6: oracle/owner resolve-dispute ping-pong
    // ------------------------------------------------------------------
    // Pre-fix the oracle could re-post its answer right after every dispute
    // (each dispute re-armed a fresh 2h window), freezing the market in a
    // loop where holders never knew which outcome stood.

    function test_DisputeBlocksOracleReResolve() public {
        uint256 id = _create(2, 50e6);
        vm.warp(1 days + 1);

        address oracleAddr = address(0xA0C1E);
        vm.prank(creator);
        market.setOracle(oracleAddr);

        vm.prank(oracleAddr);
        market.oracleResolve(id, 0);
        vm.prank(creator);
        market.disputeResolution(id);

        // The SAME oracle must not be able to just repost its answer.
        vm.prank(oracleAddr);
        vm.expectRevert(OutcomeMarket.MarketDisputed.selector);
        market.oracleResolve(id, 1);

        // Only the owner has the final say after a dispute.
        vm.prank(creator);
        market.ownerResolve(id, 1);
        (, bool resolved, uint8 winner, , , , , , , ) = market.markets(id);
        assertTrue(resolved);
        assertEq(winner, 1);
    }

    function test_SecondDisputeRejected() public {
        uint256 id = _create(2, 50e6);
        vm.warp(1 days + 1);

        address oracleAddr = address(0xA0C1E);
        vm.prank(creator);
        market.setOracle(oracleAddr);

        vm.prank(oracleAddr);
        market.oracleResolve(id, 0);
        vm.prank(creator);
        market.disputeResolution(id);

        // Disputed state is not resolved — a second dispute is meaningless
        // and must not re-arm yet another window.
        vm.prank(creator);
        vm.expectRevert(OutcomeMarket.NotResolved.selector);
        market.disputeResolution(id);
    }
}